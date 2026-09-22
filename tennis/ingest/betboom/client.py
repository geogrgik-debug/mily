"""Recorder for the BetBoom live betting line.

What this talks to
------------------
BetBoom's sportsbook is a white-label of the sporthub.bet platform. The line
is served only over a protobuf WebSocket:

    wss://{partner}-ws2.sporthub.bet:443/api/tree_ws/v1

There is no HTTP line endpoint — with the socket blocked the site renders an
empty shell. The schema in `proto/` was recovered from the shipped JS bundle
by `extract_schema.py`; see that file for how, and re-run it when the widget's
APP_BUILD changes.

Why this shape
--------------
The feed is delta-push, not polling. After a `matches_subscribe_full` you get
one full snapshot of every market on the match, then a `newsletters_stake`
message per price change carrying a CREATE/UPDATE/DELETE action, and a
`newsletters_full_match` per score change. So volume is not a rate-limit
problem: a single live tennis match emits hundreds of stake updates by itself,
and thousands of observations is a question of staying connected, not of
polling harder.

Two limits from the client's own runtime-env.js shape the driver:

* `MAX_MATCHES_SUBSCRIBE_FULL_ITEMS_LIMIT_PER_REQUEST: 1` — full-market
  subscriptions go one match per request. Many requests are fine; the cap is
  per request, not per session. How many *concurrent* full subscriptions the
  server tolerates is not stated anywhere and has to be found empirically —
  `--max-matches` exists for exactly that, start it low.
* `MAX_MATCHES_SUBSCRIBE_ITEMS_LIMIT_PER_REQUEST: 50` — the cheap
  score-and-headline-odds subscription batches fifty at a time.

Status
------
Verified here: the schema compiles and round-trips, and the transport and
message shapes are read directly off the client. NOT verified: the exact
handshake the server demands, whether game markets (`победитель следующего
гейма`, `точный счёт гейма`) are present for lower-tier events, and the
concurrency ceiling. Those need one live session, which is what `--discover`
is for: it records everything and prints the market inventory it saw.

This cannot run from a sandbox behind an HTTP CONNECT proxy: the proxy has to
pass WebSocket upgrades. Run it on a host with direct network access.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from tennis.ingest.clock import Clock
from tennis.ingest.rawlog import RawLog

DEFAULT_URL = "wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1"
ORIGIN = "https://betboom.ru"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def load_pb():
    """Import the generated protobuf module, explaining how to build it."""
    gen = Path(__file__).parent / "generated"
    sys.path.insert(0, str(gen))
    try:
        import bb_sport_ws_v1_pb2 as pb        # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "generated protobuf module missing. Build it with:\n"
            "  pip install grpcio-tools\n"
            "  python -m grpc_tools.protoc -I tennis/ingest/betboom/proto \\\n"
            "      --python_out=tennis/ingest/betboom/generated \\\n"
            "      tennis/ingest/betboom/proto/bb_sport_ws_v1.proto"
        ) from exc
    return pb


class BetBoomRecorder:
    def __init__(self, log: RawLog, *, url: str = DEFAULT_URL,
                 max_matches: int = 8, sport: str = "tennis",
                 discover: bool = False, clock: Clock | None = None):
        self.log = log
        self.url = url
        self.max_matches = max_matches
        self.sport = sport
        self.discover = discover
        self.clock = clock or Clock()
        self.pb = load_pb()
        self._uid = 0
        self.subscribed: set[int] = set()
        self.markets = Counter()
        self.stakes_seen = 0

    def uid(self, tag: str) -> str:
        self._uid += 1
        return f"{tag}-{self._uid}"

    # -- io ---------------------------------------------------------------

    async def _send(self, ws, req, *, tag: str) -> None:
        raw = req.SerializeToString()
        self.log.write(raw, direction="tx", channel="tree_ws", meta={"tag": tag})
        await ws.send(raw)

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:  # pragma: no cover
            raise SystemExit("pip install websockets")

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.url, origin=ORIGIN, max_size=32 * 1024 * 1024,
                    additional_headers={"User-Agent": USER_AGENT},
                    ping_interval=20, ping_timeout=20,
                ) as ws:
                    backoff = 1.0
                    await self._session(ws)
            except Exception as exc:
                # A dropped socket is normal operation, not an error worth
                # stopping for: the log keeps what was already written and the
                # next connection appends to it.
                self.log.write(f"reconnect after {type(exc).__name__}: {exc}",
                               direction="meta", channel="_conn")
                print(f"[conn] {type(exc).__name__}: {exc}; retry in {backoff:.0f}s",
                      file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                self.subscribed.clear()

    async def _session(self, ws) -> None:
        pb = self.pb
        req = pb.MainRequest()
        req.settings_set.uid = self.uid("set")
        req.settings_set.language = pb.LANGUAGES_RU
        await self._send(ws, req, tag="settings_set")

        req = pb.MainRequest()
        req.state_subscribe_by_sports.uid = self.uid("tree")
        req.state_subscribe_by_sports.types.append(pb.TREE_TYPES_LIVE)
        await self._send(ws, req, tag="state_subscribe_by_sports")

        asyncio.create_task(self._ping_loop(ws))

        async for frame in ws:
            if isinstance(frame, str):
                frame = frame.encode()
            self.log.write(frame, direction="rx", channel="tree_ws")
            try:
                msg = pb.MainResponse()
                msg.ParseFromString(frame)
            except Exception:
                continue                     # raw is already safe on disk
            await self._handle(ws, msg)

    async def _ping_loop(self, ws) -> None:
        pb = self.pb
        while True:
            await asyncio.sleep(20)
            try:
                req = pb.MainRequest()
                req.ping.uid = self.uid("ping")
                await self._send(ws, req, tag="ping")
            except Exception:
                return

    # -- protocol ----------------------------------------------------------

    async def _handle(self, ws, msg) -> None:
        which = msg.WhichOneof("type")
        if which == "state_subscribe_by_sports":
            for state in msg.state_subscribe_by_sports.states:
                for sport in state.sports:
                    await self._maybe_take_sport(ws, sport)
        elif which in ("newsletters_stake",):
            self._note_stake(msg.newsletters_stake.stake)
        elif which == "matches_subscribe_full":
            for item in msg.matches_subscribe_full.full_matches:
                for stake in getattr(item.match, "stakes", []):
                    self._note_stake(stake)

    async def _maybe_take_sport(self, ws, sport) -> None:
        info = sport.info
        slug = (getattr(info, "url_slug", "") or "").lower()
        name = (getattr(info, "name", "") or "").lower()
        if self.sport not in slug and self.sport not in name and "теннис" not in name:
            return
        for tournament in getattr(sport, "tournaments", []):
            for match in getattr(tournament, "matches", []):
                mid = match.info.id
                if mid in self.subscribed or len(self.subscribed) >= self.max_matches:
                    continue
                self.subscribed.add(mid)
                req = self.pb.MainRequest()
                req.matches_subscribe_full.uid = self.uid("full")
                item = req.matches_subscribe_full.full_matches.add()
                item.uid = self.uid("m")
                item.match_id = mid
                await self._send(ws, req, tag=f"subscribe_full:{mid}")
                print(f"[sub] match {mid}", file=sys.stderr)

    def _note_stake(self, stake) -> None:
        self.stakes_seen += 1
        self.markets[(stake.market_name, stake.period_name)] += 1
        if self.discover and self.stakes_seen % 200 == 0:
            self.report()

    def report(self) -> None:
        print(f"\n--- {self.stakes_seen} stake updates, "
              f"{len(self.markets)} (market, period) pairs ---", file=sys.stderr)
        for (market, period), n in self.markets.most_common(40):
            print(f"  {n:6d}  {market} | {period}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/raw", help="raw log root")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--max-matches", type=int, default=8,
                    help="concurrent full-market subscriptions; raise slowly, "
                         "the server's ceiling is undocumented")
    ap.add_argument("--sport", default="tennis")
    ap.add_argument("--discover", action="store_true",
                    help="print the market inventory as it arrives")
    args = ap.parse_args(argv)

    with RawLog(args.out, provider="betboom") as log:
        rec = BetBoomRecorder(log, url=args.url, max_matches=args.max_matches,
                              sport=args.sport, discover=args.discover)
        try:
            asyncio.run(rec.run())
        except KeyboardInterrupt:
            pass
        finally:
            rec.report()
            print(f"\n{log.frames} frames, {log.bytes} bytes -> {log.root}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
