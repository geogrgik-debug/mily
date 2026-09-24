"""Recorder for the 1win line: live tennis odds from its push server.

Why 1win at all: the lead-lag meter (`tennis.market.lead_lag`) needs a second
book's prices recorded on the same machine as BetBoom's.

How its prices travel
---------------------
Found on 23.09 by listening, in a clean browser profile, to the network of a
live match page, then repeating each call from plain Python:

* Not the "11 s json request" of the first reconnaissance. That was Kaspersky's
  web antivirus long-polling from inside the page
  (`gc.kis.v2.scr.kaspersky-labs.com/.../longp`) -- nothing of 1win's.
* A socket.io (Engine.IO 4) websocket on the API gateway, `/push-server-v2/`.
  The server opens with `0{...,"pingInterval":25000,...}`, the client answers
  `40`, the server `40{"sid":...}`. The server pings with `2`; the answer is
  `3`. No header is needed, not even Origin.
* Subscriptions go by match id, the number at the end of a match page's
  address: `42["subscribe",{"messageType":"subscribe-match-info",
  "data":{"matchIds":[...]}}]`, and the same with `subscribe-match-odds` and
  `"isBaseOddsGroups":false` for every market. The answers are
  `42["u",{"data":...,"messageType":...},"<id>"]`: `match-odds-snapshot`, the
  whole board, then `match-odds` with what changed; `match-info-snapshot` and
  `match-info` carry the score. Each odds item has a stable id, `cf` (the
  odds), `status` (1 open, 2 suspended) and `ts`, the server's milliseconds.
* Which matches are live: `POST matches/get-many` with
  `{"service":"live","sportIds":[33]}` on the same gateway. REST needs a
  browser User-Agent (403 without it); `x-lang: ru` gives Russian names beside
  the Latin slugs, and BetBoom's names are Russian.

Research and paper only: nothing here places, sizes or times a bet.

    python -m tennis.ingest.onewin.client --out data/raw [--max-matches 20]
    python -m tennis.ingest.onewin.client --probe --match 40403794

Both read `ONEWIN_API_BASE` (https://host, everything before `matches/get`)
and `ONEWIN_PARTNER_ID` (the `x-external-partner-id` every browser sends)
from the environment or flags. Neither has a default: hosts rotate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

from tennis.ingest.clock import Clock
from tennis.ingest.rawlog import RawLog

PROVIDER = "1win"
ENV_API_BASE = "ONEWIN_API_BASE"
ENV_PARTNER_ID = "ONEWIN_PARTNER_ID"
PARTNER_HEADER = "x-external-partner-id"
# Without a browser's User-Agent the gateway answers REST with 403.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
PUSH_PATH = "/push-server-v2/"
PUSH_CHANNEL = "push"
TENNIS = 33

# The REST endpoints, by the query parameter each takes.
SPORT = "sports/get"
TOURNAMENT = "tournaments/get"
MATCH = "matches/get"
LIVE = "matches/get-many"

# How often the live list is asked for. A match found this late has lost a
# minute of a match that lasts an hour or more.
DISCOVER_S = 60.0
# As in the BetBoom recorder, whose feed taught it: a session that ends sooner
# than this was refused rather than dropped, and the wait before the next
# attempt doubles up to MAX_BACKOFF_S; after one that lived, retry at once.
HEALTHY_SESSION_S = 60.0
MAX_BACKOFF_S = 60.0
# The server pings every 25 s and gives up 20 s later; this long without any
# frame is a socket that died without saying so.
SILENCE_S = 60.0
# Not singles tennis: a pair's players are one name with a slash in it, and a
# simulator names itself. BetBoom's recorder skips the same, so neither side
# would find the other's half of such a match.
SKIP_WORDS = ("пары", "парный", "doubles", "кибер", "cyber", "esports", "simulated")
# The recorder's counters sit beside its log, in its own provider=1win folder,
# and not under the `_recorder*.json` name: tennis.ingest.status reads the
# newest such file in the log root as BetBoom's.
COUNTERS_PREFIX = "_counters-"
# `quotes_last_hour` adds up this many minutes of the monotonic clock.
HOUR_MINUTES = 60


class ConfigError(ValueError):
    """The recorder was not told where the API is or who it is."""


@dataclass(frozen=True)
class OneWinConfig:
    """Where the API is and whom the requests are from.

    Nothing has a default. Hosts rotate like any bookmaker's -- the site's
    mirror already has -- so a stale host in the code would fail quietly in
    the one way a recorder must not; and the partner id is the owner's to give.
    """

    api_base: str        # scheme and host, and a path prefix if there is one
    partner_id: str
    timeout_s: float = 15.0

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None, *, api_base: str | None = None,
             partner_id: str | None = None, timeout_s: float = 15.0) -> "OneWinConfig":
        """Arguments first, then the environment; fail naming what is missing."""
        env = os.environ if env is None else env
        base = (api_base or env.get(ENV_API_BASE) or "").strip()
        partner = (partner_id or env.get(ENV_PARTNER_ID) or "").strip()
        missing = [name for name, value in ((ENV_API_BASE, base), (ENV_PARTNER_ID, partner))
                   if not value]
        if missing:
            raise ConfigError(f"missing {', '.join(missing)}: set it in the environment "
                              "or pass --api-base / --partner-id "
                              "(see tennis/ingest/onewin/README.md)")
        parts = urlsplit(base)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise ConfigError(f"{ENV_API_BASE} must be https://host[/prefix] with no "
                              f"query, got {base!r}")
        return cls(base.rstrip("/"), partner, timeout_s)

    def push_url(self) -> str:
        """The websocket of the push server, on the same gateway."""
        parts = urlsplit(self.api_base)
        query = urlencode({"Language": "ru", "externalPartnerId": self.partner_id,
                           "EIO": "4", "transport": "websocket"})
        return f"wss://{parts.netloc}{parts.path}{PUSH_PATH}?{query}"


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    cache_control: str | None = None

    def json(self):
        return json.loads(self.body)


# (url, headers, timeout_s, body or None) -> (status, response headers, body)
Transport = Callable[[str, dict, float, "bytes | None"], tuple[int, Mapping[str, str], bytes]]


def urllib_transport(url: str, headers: dict, timeout_s: float, data: bytes | None = None):
    """GET, or POST when there is a body, with the standard library. An HTTP
    error is an answer, not a crash: 404 was a finding of the reconnaissance,
    and it belongs in the log."""
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers or {}, err.read()


class OneWinClient:
    """The REST side of the gateway. Every answer is on disk before anything
    reads it -- the collector's first rule, as for BetBoom. The partner id
    travels in a header and never into the log."""

    def __init__(self, config: OneWinConfig, log: RawLog,
                 transport: Transport = urllib_transport):
        self.config = config
        self.log = log
        self.transport = transport

    def _headers(self) -> dict:
        return {PARTNER_HEADER: self.config.partner_id, "Accept": "application/json",
                "User-Agent": USER_AGENT, "x-lang": "ru"}

    def _answer(self, endpoint: str, url: str, status: int, resp_headers, body: bytes) -> Response:
        cache = {k.lower(): v for k, v in dict(resp_headers).items()}.get("cache-control")
        self.log.write(body, direction="rx", channel=endpoint,
                       meta={"status": status, "url": url, "cache_control": cache})
        return Response(url, status, body, cache)

    def get(self, endpoint: str, **params) -> Response:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.config.api_base}/{endpoint}" + (f"?{query}" if query else "")
        self.log.write(url, direction="tx", channel=endpoint)
        status, headers, body = self.transport(url, self._headers(), self.config.timeout_s, None)
        return self._answer(endpoint, url, status, headers, body)

    def post(self, endpoint: str, payload: dict) -> Response:
        url = f"{self.config.api_base}/{endpoint}"
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.log.write(data.decode(), direction="tx", channel=endpoint, meta={"url": url})
        headers = {**self._headers(), "Content-Type": "application/json"}
        status, resp_headers, body = self.transport(url, headers, self.config.timeout_s, data)
        return self._answer(endpoint, url, status, resp_headers, body)

    def sport(self, sport_id) -> Response:
        return self.get(SPORT, sportId=sport_id)

    def tournament(self, tournament_id) -> Response:
        return self.get(TOURNAMENT, tournamentId=tournament_id)

    def match(self, match_id) -> Response:
        return self.get(MATCH, matchId=match_id)

    def live_tennis(self) -> Response:
        return self.post(LIVE, {"limit": 100, "service": "live", "sportIds": [TENNIS]})


def singles(items) -> list[int]:
    """Ids of the live matches worth a slot: tennis singles, no simulator."""
    out = []
    for m in items:
        players = [c.get("name") or "" for c in m.get("competitors") or []]
        where = [m.get(k) or {} for k in ("category", "tournament")]
        words = " ".join([m.get("name") or "", m.get("slug") or "",
                          *(str(w.get(k) or "") for w in where for k in ("slug", "name"))]).lower()
        if (m.get("sportId") == TENNIS and len(players) == 2 and all(players)
                and not any("/" in p for p in players)
                and not any(word in words for word in SKIP_WORDS)):
            out.append(m["id"])
    return out


def describe_exc(exc: BaseException) -> str:
    """The error and its cause: websockets puts the peer's reset or EOF there."""
    why = f"{type(exc).__name__}: {exc}"
    if exc.__cause__ is not None:
        why += f" <- {type(exc.__cause__).__name__}: {exc.__cause__}"
    return why


class OneWinRecorder:
    """Same shape as the BetBoom recorder: one raw log, one loop, one report.

    Every frame of the push socket is written before it is read. Subscriptions
    live on the socket, so a reconnect asks for all of them again, and writes
    a `_conn` meta row -- the break `tennis.market.streams` reads it as.

    Once a minute it writes its counters beside the log (`counters_path`): a
    growing file proves only that something arrives, and the server's pings
    alone make it grow -- the way BetBoom's capture stood idle on 22-23.09.
    """

    def __init__(self, client: OneWinClient, *, max_matches: int = 20,
                 clock: Clock | None = None):
        self.client = client
        self.log = client.log
        self.max_matches = max_matches
        self.clock = clock or Clock()
        self.live: list[int] = []            # live singles, in the gateway's order
        self.subscribed: set[int] = set()    # on the current socket
        self.kinds: Counter = Counter()
        self.reconnects = 0
        self.last_disconnect: str | None = None
        self.disconnects: deque[dict] = deque(maxlen=10)
        self.discover_errors = 0
        self.quotes_total = 0                # odds items of every price message
        self.last_quote_at_s: float | None = None
        self._quotes_by_minute: Counter = Counter()   # monotonic minute -> quotes
        self._sleep = asyncio.sleep          # the wait between sessions; tests replace it

    # -- REST ---------------------------------------------------------------

    def probe(self, *, sports=(), tournaments=(), matches=()) -> list[Response]:
        """Fetch each named object once. Proves the config from this machine."""
        out = [self.client.sport(i) for i in sports]
        out += [self.client.tournament(i) for i in tournaments]
        out += [self.client.match(i) for i in matches]
        return out

    def discover(self) -> list[int]:
        """Ask which singles are live. A failed ask keeps the last answer: a
        live list a minute old beats unsubscribing everything."""
        try:
            resp = self.client.live_tennis()
            items = resp.json()["result"]["items"] if resp.status == 200 else None
        except Exception as exc:
            items = None
            print(f"[live] {describe_exc(exc)}", file=sys.stderr)
        if items is None:
            self.discover_errors += 1
            return self.live
        self.live = singles(items)
        return self.live

    # -- the push socket ------------------------------------------------------

    async def _send(self, ws, text: str) -> None:
        self.log.write(text, direction="tx", channel=PUSH_CHANNEL)
        await ws.send(text)

    async def _subscribe(self, ws, ids: list[int]) -> None:
        fresh = [i for i in ids if i not in self.subscribed][: self.max_matches - len(self.subscribed)]
        if not fresh:
            return
        for kind, extra in (("subscribe-match-info", {}),
                            ("subscribe-match-odds", {"isBaseOddsGroups": False})):
            body = {"messageType": kind, "data": {"matchIds": fresh, **extra}}
            await self._send(ws, "42" + json.dumps(["subscribe", body], separators=(",", ":")))
        self.subscribed.update(fresh)
        print(f"[sub] {len(fresh)} match(es): {fresh}", file=sys.stderr)

    async def _follow_live(self, ws, connected: asyncio.Event, every: float = DISCOVER_S) -> None:
        """Keep the subscriptions on the live singles: finished matches give
        their slot back, new ones take free slots."""
        await connected.wait()
        while True:
            live = await asyncio.to_thread(self.discover)
            self.subscribed &= set(live)
            await self._subscribe(ws, live)
            await asyncio.sleep(every)

    async def _session(self, ws, silence_s: float = SILENCE_S) -> None:
        connected = asyncio.Event()
        follow = asyncio.create_task(self._follow_live(ws, connected))
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=silence_s)
                except asyncio.TimeoutError:
                    raise ConnectionError(f"no frame for {silence_s:g} s") from None
                if isinstance(frame, str):
                    # The one change to a raw frame: a refusal may echo the
                    # socket's address, and the partner id stays out of the log.
                    frame = frame.replace(self.client.config.partner_id, "<partner id>")
                self.log.write(frame, direction="rx", channel=PUSH_CHANNEL)
                if not isinstance(frame, str):
                    continue
                if frame == "2":                          # Engine.IO ping
                    await self._send(ws, "3")
                elif frame.startswith("0"):               # open: connect the namespace
                    await self._send(ws, "40")
                elif frame.startswith("40"):
                    connected.set()
                elif frame.startswith(("41", "44")):      # disconnected, or refused
                    raise ConnectionError(f"the server ended the session: {frame[:200]}")
                elif frame.startswith("42"):
                    kind, quotes = _message(frame)
                    self.kinds[kind] += 1
                    if quotes:
                        self._count_quotes(quotes)
        finally:
            follow.cancel()

    # -- counters -------------------------------------------------------------

    def _count_quotes(self, n: int) -> None:
        now = self.clock.now()
        self.quotes_total += n
        self.last_quote_at_s = now.wall_s
        self._quotes_by_minute[now.mono_ns // 60_000_000_000] += n

    def quotes_last_hour(self) -> int:
        """Quotes of the last HOUR_MINUTES minutes, the current one included."""
        minute = self.clock.now().mono_ns // 60_000_000_000
        for old in [m for m in self._quotes_by_minute if m <= minute - HOUR_MINUTES]:
            del self._quotes_by_minute[old]
        return sum(self._quotes_by_minute.values())

    def counters(self) -> dict:
        return {
            "provider": PROVIDER,
            "run_id": self.log.run_id,
            "written_at_s": self.clock.now().wall_s,
            "live": len(self.live),
            "subscribed": len(self.subscribed),
            "quotes_last_hour": self.quotes_last_hour(),
            "quotes_total": self.quotes_total,
            "last_quote_at_s": self.last_quote_at_s,
            "frames": self.log.frames,
            "reconnects": self.reconnects,
            "last_disconnect": self.last_disconnect,
        }

    def counters_path(self) -> Path:
        return (Path(self.log.root) / f"provider={PROVIDER}"
                / f"{COUNTERS_PREFIX}{self.log.run_id}.json")

    def _write_counters(self) -> None:
        """Write-then-rename, so a reader never catches half a file. A failed
        write is skipped: the log is the thing that must not stop."""
        try:
            path = self.counters_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.counters(), ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def _remove_counters(self) -> None:
        """A recorder that has stopped must not leave counters claiming it runs.
        Only a kill leaves them, and their `written_at_s` ages them out."""
        try:
            self.counters_path().unlink(missing_ok=True)
        except OSError:
            pass

    async def run(self, connect=None) -> None:
        if connect is None:
            try:
                import websockets
            except ImportError:  # pragma: no cover
                raise SystemExit("pip install websockets")
            url = self.client.config.push_url()

            def connect():
                return websockets.connect(url, max_size=32 * 1024 * 1024,
                                          ping_interval=None, open_timeout=20)

        heartbeat = asyncio.create_task(self._heartbeat_loop())
        backoff = 1.0
        try:
            while True:
                opened = None
                why = "closed by the server"
                try:
                    async with connect() as ws:
                        opened = self.clock.now()
                        await self._session(ws)
                except Exception as exc:
                    why = describe_exc(exc)
                # The socket's address carries the partner id; so may its errors.
                why = why.replace(self.client.config.partner_id, "<partner id>")
                lived = self.clock.now() - opened if opened is not None else None
                self.reconnects += 1
                self.last_disconnect = why[:300]
                self.disconnects.append({"why": why[:300],
                                         "lived_s": None if lived is None else round(lived, 1)})
                if lived is not None and lived >= HEALTHY_SESSION_S:
                    backoff = 0.0
                self.log.write(f"reconnect after {why}", direction="meta", channel="_conn")
                print(f"[conn] {why}; retry in {backoff:.0f}s", file=sys.stderr)
                self.subscribed.clear()
                await self._sleep(backoff)
                backoff = min(max(backoff * 2, 1.0), MAX_BACKOFF_S)
        finally:
            heartbeat.cancel()
            self._remove_counters()

    async def _heartbeat_loop(self, every: float = 60.0) -> None:
        """Say what has arrived, so a quiet capture is not mistaken for a dead
        one; and write the counters, the first time at once, so a restarted
        capture is not without them for a minute."""
        self._write_counters()
        while True:
            await asyncio.sleep(every)
            kinds = ", ".join(f"{k}={n}" for k, n in self.kinds.most_common(5))
            print(f"[hb] {self.log.frames} frames, {len(self.subscribed)} subscribed of "
                  f"{len(self.live)} live singles, {self.reconnects} reconnects, "
                  f"{self.quotes_last_hour()} quotes in the last hour "
                  f"| {kinds or 'no messages yet'}", file=sys.stderr)
            self._write_counters()


def _message(frame: str) -> tuple[str, int]:
    """A socket.io event's type, and how many quotes it carries.

    A quote is one outcome's odds item. On 23.09 (15 minutes, 22 matches) only
    `match-odds-snapshot` and `match-odds` carried any; the score came in
    `match-info` nearly as often (1469 messages to 1571), and the server's
    pings every 25 s. A socket that brings only those grows its file and
    prices nothing.
    """
    try:
        msg = json.loads(frame[2:])
        body = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
        kind = body.get("messageType") or str(msg[0])
    except (ValueError, IndexError, TypeError, KeyError):
        return "unreadable", 0
    data = body.get("data")
    groups = data.get("oddsGroups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return kind, 0
    return kind, sum(len(g.get("oddsList") or []) for g in groups if isinstance(g, dict))


def describe(resp: Response) -> str:
    """One line per answer: status, size, caching, and its top-level keys --
    read off the body, not assumed."""
    line = f"{resp.status}  {len(resp.body):6d} B  cache-control={resp.cache_control}  {resp.url}"
    try:
        doc = resp.json()
    except ValueError:
        return line + "\n      body is not JSON"
    if isinstance(doc, dict):
        return line + f"\n      keys: {', '.join(sorted(doc))}"
    return line + f"\n      top level: {type(doc).__name__}"


async def _bounded(run, seconds: float | None) -> None:
    if seconds is None:
        await run
        return
    try:
        await asyncio.wait_for(run, seconds)
    except asyncio.TimeoutError:
        pass


def main(argv: list[str] | None = None, *, transport: Transport = urllib_transport) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/raw", help="raw log root")
    ap.add_argument("--api-base", help=f"https://host; else ${ENV_API_BASE}")
    ap.add_argument("--partner-id", help=f"else ${ENV_PARTNER_ID}")
    ap.add_argument("--max-matches", type=int, default=20,
                    help="live singles subscribed at once")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop after this long, for a probe or a trial; default: until stopped")
    ap.add_argument("--probe", action="store_true",
                    help="fetch the named objects once, print what came back, record nothing live")
    ap.add_argument("--sport", action="append", default=[], metavar="ID")
    ap.add_argument("--tournament", action="append", default=[], metavar="ID")
    ap.add_argument("--match", action="append", default=[], metavar="ID")
    args = ap.parse_args(argv)

    if args.probe and not (args.sport or args.tournament or args.match):
        ap.error("--probe needs at least one --sport, --tournament or --match id")
    try:
        config = OneWinConfig.load(api_base=args.api_base, partner_id=args.partner_id)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    with RawLog(args.out, provider=PROVIDER) as log:
        recorder = OneWinRecorder(OneWinClient(config, log, transport),
                                  max_matches=args.max_matches)
        if args.probe:
            for resp in recorder.probe(sports=args.sport, tournaments=args.tournament,
                                       matches=args.match):
                print(describe(resp))
            print(f"\n{log.frames} frames -> {log.root}", file=sys.stderr)
            return 0
        print(f"[1win] recording live tennis singles -> {log.root} (Ctrl+C stops)",
              file=sys.stderr)
        try:
            asyncio.run(_bounded(recorder.run(), args.seconds))
        except KeyboardInterrupt:
            pass
        print(f"\n{log.frames} frames -> {log.root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
