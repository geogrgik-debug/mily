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
one full snapshot of every market on the match, and a `newsletters_full_match`
with the whole board again **on every score change** -- not on a timer. So
volume is not a rate-limit problem: a single live tennis match emits hundreds
of stake updates by itself, and thousands of observations is a question of
staying connected, not of polling harder.

A price change on its own moves no score, so it pushes nothing. That is why
`stakes_subscribe` is sent as well: it names individual outcomes and makes the
server push a `newsletters_stake` per change to each. It was previously assumed
to arrive unasked, and it does not -- 27,000 captured frames contained zero of
them. Measured 2026-09-22 over 7 minutes on 16 game outcomes: 97% of value
changes reached the per-stake stream first, median 0.5 s ahead of the snapshot
that eventually carried them, p90 78 s, max 153 s, and one change no snapshot
ever showed. Without this subscription a capture is blind to price movement
inside a game, which is the one movement this project exists to measure.

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
import json
import sys
from collections import Counter
from pathlib import Path

from tennis.ingest.clock import Clock
from tennis.ingest.rawlog import RawLog
from tennis.market.names import parse_market

DEFAULT_URL = "wss://ru-ws2.sporthub.bet:443/api/tree_ws/v1"

# A subscribed match that has priced nothing for this long gives its slot back.
# A game lasts minutes and every score change re-sends the board, so twenty
# minutes of silence is a long rain break or a match the feed never announced
# as over. It is not taken again for the cooldown, so a paused match cannot
# cycle through a slot doing nothing.
STALE_MATCH_S = 20 * 60
STALE_COOLDOWN_S = 30 * 60

# Tournaments inside the tennis tree that are not what a serve model wants.
# Measured on a live tree: 12 of 32 tournaments -- 11 doubles and one
# simulator -- 37% of what --max-matches would otherwise spend its slots on.
# Doubles are singles' rules on a different game; the simulator is not tennis.
DOUBLES_MARKERS = ("пары", "doubles", "пара ", "/ ")
SIM_MARKERS = ("esportsbattle", "etennis", "кибертеннис", "cybertennis", "simulated")
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
                 discover: bool = False, clock: Clock | None = None,
                 time_filter: str = ""):
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
        # Diagnostics. A first live session can end with frames on disk and
        # nothing subscribed, and then the only question that matters is what
        # the server actually sent. Counting every response type costs nothing
        # and makes silence informative.
        self.kinds = Counter()
        self.sports_seen = Counter()
        self.last_stake_at: float | None = None
        self.reconnects = 0
        self.errors: list[str] = []
        self.bad_codes: list[str] = []
        # The live tree is lazy, so each layer is asked for exactly once.
        self.time_filter = time_filter
        self.sports_asked: set[int] = set()
        self.categories_asked: set[tuple[int, int]] = set()
        self.tournaments_asked: set[int] = set()
        self.match_tier: dict[int, tuple[str, str]] = {}
        self.skipped = Counter()          # why a tournament was not subscribed
        self.include_doubles = False
        self.include_sim = False
        # Outcomes followed one by one, so a reprice that moves no score is
        # still pushed. Batched at 40 per request: the platform documents no
        # limit for stakes, and 40 is the largest batch seen accepted live.
        self.stakes_asked: set[str] = set()
        self.stake_pushes = 0
        # Slots. `subscribed` used to only ever grow: once the first
        # max_matches matches ended, every later one was turned away, and on
        # the night of 22-23.09 the capture spent hours writing the tour-wide
        # score stream and not one price, "10 subscribed" all along. A match
        # now gives its slot back when the feed deletes it from the live tree,
        # marks it finished, or goes silent (STALE_MATCH_S), and the next live
        # match from the tour-wide stream takes the slot.
        self.seen_at: dict[int, float] = {}        # subscribed match -> last price activity
        self.stakes_of: dict[int, set[str]] = {}   # subscribed match -> outcomes followed
        self.done: set[int] = set()                # finished, removed or unwanted: never again
        self.cooldown: dict[int, float] = {}       # released as silent: not before this time
        self.released = Counter()                  # why slots were given back
        self.unwanted_tournaments: set[int] = set()

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
                self.reconnects += 1
                self.log.write(f"reconnect after {type(exc).__name__}: {exc}",
                               direction="meta", channel="_conn")
                print(f"[conn] {type(exc).__name__}: {exc}; retry in {backoff:.0f}s",
                      file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                self._forget_subscriptions()

    def _forget_subscriptions(self) -> None:
        """Drop every memory of what we asked for, before reconnecting.

        A new socket carries none of the old subscriptions, so all of it has to
        be asked for again. Clearing only `subscribed` -- which is what this
        did -- left the layer sets populated, so `_take_sport` saw the sport as
        already requested and returned early. The recorder then sat connected,
        writing the cheap tour-wide score stream and not one price, for as long
        as it was left running.

        Observed live: one dropped socket, reconnect succeeded, and the capture
        recorded no odds at all from that moment. It looks healthy from outside
        -- the log keeps growing -- which is exactly what makes it dangerous.
        """
        self.subscribed.clear()
        self.sports_asked.clear()
        self.categories_asked.clear()
        self.tournaments_asked.clear()
        self.stakes_asked.clear()
        # Per-socket too. What is known about matches -- finished, unwanted,
        # cooling down -- stays true across a reconnect and is kept.
        self.seen_at.clear()
        self.stakes_of.clear()

    async def _session(self, ws) -> None:
        pb = self.pb
        # settings_set is skipped unless a time filter is supplied. Sending it
        # without one is rejected: the server answers code 400 with a violation
        # on `time_filter`, and the value it wants is not in the schema (a bare
        # string with no enum beside it). Nothing is lost -- the tree comes back
        # code 200 regardless, and already in Russian, which is the only reason
        # the call was there.
        if self.time_filter:
            req = pb.MainRequest()
            req.settings_set.uid = self.uid("set")
            req.settings_set.language = pb.LANGUAGES_RU
            req.settings_set.time_filter = self.time_filter
            await self._send(ws, req, tag="settings_set")

        req = pb.MainRequest()
        req.state_subscribe_by_sports.uid = self.uid("tree")
        req.state_subscribe_by_sports.types.append(pb.TREE_TYPES_LIVE)
        await self._send(ws, req, tag="state_subscribe_by_sports")

        asyncio.create_task(self._ping_loop(ws))
        # Always, not only under --discover. The long-running capture is the
        # mode that most needs to say it is alive: a silent process is
        # indistinguishable from a hung one, and that is the mode left running
        # for days. --discover only makes it chattier.
        asyncio.create_task(self._heartbeat_loop(15.0 if self.discover else 60.0))
        asyncio.create_task(self._release_loop(ws))

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

    async def _heartbeat_loop(self, every: float = 15.0) -> None:
        """Say what has arrived, so a session with no stakes is not silent.

        The market inventory only prints every 200 stake updates, which never
        fires when the count is zero -- the case that most needs explaining.
        """
        while True:
            await asyncio.sleep(every)
            kinds = ", ".join(f"{k}={n}" for k, n in self.kinds.most_common(6))
            print(f"[hb] {self.log.frames} frames, "
                  f"{len(self.subscribed)} subscribed, "
                  f"{len(self.stakes_asked)} outcomes followed, "
                  f"{self.stakes_seen} stakes, "
                  f"{self.stake_pushes} per-stake pushes, "
                  f"{sum(self.released.values())} slots released "
                  f"| {kinds or 'no responses yet'}",
                  file=sys.stderr)
            self._write_sidecar()

    def sidecar_path(self) -> Path:
        return Path(self.log.root) / f"_recorder-{self.log.run_id}.json"

    def _remove_sidecar(self) -> None:
        """A recorder that exits cleanly must not leave a status file behind
        claiming to be alive. Only a crash leaves one, and status.py ages it out."""
        try:
            self.sidecar_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _write_sidecar(self) -> None:
        """Publish live internal state beside the log, for an outside checker.

        A growing log file is not proof the capture is working: the feed pushes
        a tour-wide score stream whether or not anything is subscribed, so a
        recorder that lost its subscriptions still writes. The counters that
        distinguish the two live only in this process, so it writes them out.

        Cheap by design -- a few hundred bytes every heartbeat, no
        decompression -- so a checker can run every few minutes.
        """
        try:
            # One file per run. A single shared name meant the last recorder to
            # exit -- a two-minute probe -- overwrote the live capture's status
            # with its own, and an outside checker would have called the live
            # one dead. Stale files from crashes are ignored by age in status.py.
            path = self.sidecar_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "run_id": self.log.run_id,
                "written_at_s": self.clock.now().wall_ns / 1e9,
                "frames": self.log.frames,
                "subscribed": len(self.subscribed),
                "stakes_seen": self.stakes_seen,
                "stakes_followed": len(self.stakes_asked),
                "stake_pushes": self.stake_pushes,
                "last_stake_at_s": self.last_stake_at,
                "reconnects": self.reconnects,
                "errors": len(self.errors),
                "bad_codes": len(self.bad_codes),
                "released": dict(self.released),
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            # Never let a status write kill a capture. The sidecar is a
            # convenience; the log is the thing that must not stop.
            pass

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
        self.kinds[which or "(no oneof set)"] += 1
        if which is None:
            return
        body = getattr(msg, which)
        self._check_code(which, body)

        if which == "error":
            # Previously swallowed: a refused subscription looked exactly like
            # an idle socket, which is the worst possible failure mode here.
            text = str(body).strip().replace("\n", " ")
            self.errors.append(text)
            print(f"[error] {text}", file=sys.stderr)
            return

        # -- the tree, layer by layer.
        #
        # The live tree is lazy and arrives in three steps, which is why an
        # earlier version sat silent with frames on disk and nothing
        # subscribed: it read `sport.tournaments` straight off the sports
        # response, and the server sends the sports layer with counts only.
        # Measured on a live session: 16 sports, tennis carrying
        # matches_count=62 and tournaments_count=28, and not one tournament
        # inline. Each layer below is also delivered as a newsletters_* push,
        # so both paths feed the same handlers.
        if which == "state_subscribe_by_sports":
            for state in body.states:
                for sport in state.sports:
                    await self._take_sport(ws, sport)
        elif which == "newsletters_sport":
            await self._take_sport(ws, body.sport)
        elif which == "state_subscribe_sports":
            for item in body.sports:
                self._check_code("state_subscribe_sports.item", item)
                if item.HasField("sport"):
                    await self._take_sport(ws, item.sport)
        elif which == "state_subscribe_by_categories":
            for state in body.states:
                for category in state.categories:
                    await self._take_category(ws, category)
        elif which == "state_subscribe_categories":
            for state in getattr(body, "states", []):
                for category in getattr(state, "categories", []):
                    await self._take_category(ws, category)
        elif which == "newsletters_category":
            await self._take_category(ws, body.category)
        elif which == "state_subscribe_tournaments":
            for state in getattr(body, "states", []):
                for tournament in getattr(state, "tournaments", []):
                    await self._take_tournament(ws, tournament)
        elif which in ("newsletters_tournament", "newsletters_full_tournament"):
            await self._take_tournament(ws, body.tournament)
        elif which == "newsletters_match":
            if body.action == self.pb.NEWSLETTER_ACTIONS_DELETE:
                await self._finish(ws, body.match.info.id, "deleted")
            else:
                await self._take_match(ws, body.match)
        elif which == "newsletters_full_match":
            self._note_match(body.match)
            mid = body.match.info.id
            if body.action == self.pb.NEWSLETTER_ACTIONS_DELETE:
                await self._finish(ws, mid, "deleted")
            elif self._is_over(body.match):
                await self._finish(ws, mid, "finished")
            else:
                self._touch(mid)
                await self._take_stakes(ws, body.match)
        elif which == "newsletters_stake":
            self.stake_pushes += 1
            self._note_stake(body.stake)
            self._touch(body.stake.match_id)
        elif which == "stakes_subscribe":
            for item in body.stakes:
                self._check_code("stakes_subscribe.item", item)
        elif which == "matches_subscribe_full":
            for item in body.full_matches:
                self._check_code("matches_subscribe_full.item", item)
                mid = item.match.info.id
                if mid and (item.HasField("tournament") or item.HasField("category")):
                    self.match_tier[mid] = (item.category.info.name,
                                            item.tournament.info.name)
                self._note_match(item.match)
                # A match taken from the tour-wide stream arrives without its
                # tournament; the reply names it, so the tier filter applies
                # here too.
                reason = self._unwanted_names(item.tournament.info.name,
                                              item.category.info.name)
                if mid in self.subscribed and reason:
                    if item.tournament.info.id:
                        self.unwanted_tournaments.add(item.tournament.info.id)
                    await self._finish(ws, mid, reason)
                    continue
                self._touch(mid)
                await self._take_stakes(ws, item.match)

    def _check_code(self, which: str, body) -> None:
        """Report a non-200 status on any typed response.

        The `error` member of MainResponse is not the only way a refusal
        arrives: a typed response carries its own code/status/error, and that
        is how the real server rejected settings_set -- code 400, message
        "Данные не прошли валидацию", with the offending field named in the
        details. That went unnoticed because nothing looked at `code`.
        """
        code = getattr(body, "code", None)
        if code in (None, 0, 200):
            return
        detail = f"{which} code={code}"
        message = (getattr(getattr(body, "error", None), "message", "") or "").strip()
        if message:
            detail += f": {message}"
        for violation in self._violations(getattr(body, "error", None)):
            detail += f" [{violation}]"
        self.bad_codes.append(detail)
        print(f"[bad] {detail}", file=sys.stderr)

    def _violations(self, error) -> list[str]:
        """Field-level violations out of the error's packed details, if any."""
        out: list[str] = []
        details = getattr(error, "details", None)
        if details is None:
            return out
        for any_msg in (details if hasattr(details, "__iter__") else [details]):
            value = getattr(any_msg, "value", b"")
            if not value:
                continue
            try:
                parsed = self.pb.common_BadRequestErrorDetails()
                parsed.ParseFromString(value)
            except Exception:
                continue
            for v in parsed.violations:
                out.append(f"{v.reason}: {v.message}".strip(": "))
        return out

    # -- tree walk ---------------------------------------------------------

    async def _take_sport(self, ws, sport) -> None:
        info = sport.info
        slug = (getattr(info, "url_slug", "") or "").lower()
        name = (getattr(info, "name", "") or "").lower()
        tournaments = list(getattr(sport, "tournaments", []))
        n_matches = sum(len(getattr(t, "matches", [])) for t in tournaments)
        self.sports_seen[(info.name or slug or str(info.id),
                          len(tournaments), n_matches)] += 1
        if not self._is_wanted_sport(slug, name):
            return

        for tournament in tournaments:
            await self._take_tournament(ws, tournament)

        if info.id in self.sports_asked:
            return
        self.sports_asked.add(info.id)
        # Measured live: state_subscribe_by_categories is the esports path only
        # (sport_id must be 1, and the categories it returns are Dota 2, CS2,
        # R6...). A real sport is subscribed with state_subscribe_sports, whose
        # reply carries the whole tree -- ~100 KB for tennis, 27 tournaments,
        # 68 matches -- and is followed by newsletters_match pushes.
        req = self.pb.MainRequest()
        req.state_subscribe_sports.uid = self.uid("sport")
        item = req.state_subscribe_sports.sports.add()
        item.uid = self.uid("s")
        item.type = self.pb.TREE_TYPES_LIVE
        item.sport_id = info.id
        await self._send(ws, req, tag=f"subscribe_sport:{info.id}")
        print(f"[tree] sport {info.name!r} id={info.id} "
              f"tournaments={getattr(info, 'tournaments_count', 0)} "
              f"matches={getattr(info, 'matches_count', 0)} -> subscribing",
              file=sys.stderr)

    def _is_wanted_sport(self, slug: str, name: str) -> bool:
        """Exact match on the slug, or on the name as a fallback.

        This used to be a substring test, and the first end-to-end run filled
        all six slots with Setka Cup -- table tennis, whose name contains
        "теннис" and whose tree happened to arrive before tennis did. The
        tennis sport carries url_slug 'tennis' exactly (id 4 on this feed).
        """
        want = self.sport.lower()
        names = {"tennis": {"tennis", "теннис"}}.get(want, {want})
        return slug == want or name in names

    async def _take_category(self, ws, category) -> None:
        info = category.info
        tournaments = list(getattr(category, "tournaments", []))
        for tournament in tournaments:
            await self._take_tournament(ws, tournament)
        if tournaments or not getattr(info, "tournaments_count", 0):
            return
        key = (getattr(info, "sport_id", 0), info.id)
        if key in self.categories_asked:
            return
        self.categories_asked.add(key)
        req = self.pb.MainRequest()
        req.state_subscribe_categories.uid = self.uid("cat")
        item = req.state_subscribe_categories.categories.add()
        item.uid = self.uid("c")
        item.type = self.pb.TREE_TYPES_LIVE
        item.sport_id = getattr(info, "sport_id", 0)
        item.category_id = info.id
        await self._send(ws, req, tag=f"subscribe_category:{info.id}")

    def _unwanted(self, tournament) -> str | None:
        """Why a tournament should not spend a subscription slot, or None.

        Judged from the tournament's own name and category, because that is
        where the feed says it: "WTT 25. Сетубал. Хард. Пары" is doubles, and
        "ESportsBattle eTennis" sits in category "Кибертеннис". Nothing in the
        match record itself distinguishes them.

        This only decides what to *subscribe* to. Anything the feed pushes
        anyway is still recorded raw -- the rule that bytes land before
        anyone judges them is not bent here.
        """
        info = tournament.info
        cat = ""
        if tournament.HasField("category"):
            cat = tournament.category.info.name or ""
        return self._unwanted_names(getattr(info, "name", "") or "", cat)

    def _unwanted_names(self, name: str, cat: str) -> str | None:
        name, cat = (name or "").lower(), (cat or "").lower()
        if not self.include_sim and (
                any(m in name for m in SIM_MARKERS) or any(m in cat for m in SIM_MARKERS)):
            return "simulator"
        if not self.include_doubles and any(m in name for m in DOUBLES_MARKERS):
            return "doubles"
        return None

    async def _take_tournament(self, ws, tournament) -> None:
        info = tournament.info
        reason = self._unwanted(tournament)
        if reason:
            self.skipped[(reason, info.name)] += 1
            if info.id:
                self.unwanted_tournaments.add(info.id)
            return
        matches = list(getattr(tournament, "matches", []))
        for match in matches:
            await self._take_match(ws, match)
        if matches or not getattr(info, "matches_count", 0):
            return
        if info.id in self.tournaments_asked:
            return
        self.tournaments_asked.add(info.id)
        req = self.pb.MainRequest()
        req.state_subscribe_tournaments.uid = self.uid("tours")
        item = req.state_subscribe_tournaments.tournaments.add()
        item.uid = self.uid("t")
        item.type = self.pb.TREE_TYPES_LIVE
        item.tournament_id = info.id
        await self._send(ws, req, tag=f"subscribe_tournament:{info.id}")

    async def _take_match(self, ws, match) -> None:
        mid = match.info.id
        if mid and self._is_over(match):
            await self._finish(ws, mid, "finished")
            return
        if not mid or mid in self.subscribed:
            self._note_match(match)
            return
        if (mid in self.done or self.cooldown.get(mid, 0.0) > self._now()
                or match.info.tournament_id in self.unwanted_tournaments):
            return
        if len(self.subscribed) >= self.max_matches:
            return
        self.subscribed.add(mid)
        self.seen_at[mid] = self._now()
        req = self.pb.MainRequest()
        req.matches_subscribe_full.uid = self.uid("full")
        item = req.matches_subscribe_full.full_matches.add()
        item.uid = self.uid("m")
        item.match_id = mid
        await self._send(ws, req, tag=f"subscribe_full:{mid}")
        print(f"[sub] match {mid}", file=sys.stderr)
        self._note_match(match)

    async def _take_stakes(self, ws, match) -> None:
        """Follow this match's game outcomes one by one.

        Only game markets, because they are what the project models and a
        whole board would be some 126 subscriptions per match. The ids come
        from the snapshot that just arrived, so nothing is guessed; each is
        asked for once, and a reconnect forgets them all.
        """
        mid = match.info.id
        if not mid or mid not in self.subscribed:
            return
        fresh = [s.stake_id for s in getattr(match, "stakes", [])
                 if s.stake_id and s.stake_id not in self.stakes_asked
                 and parse_market(s.market_name).is_game]
        if not fresh:
            return
        self.stakes_asked.update(fresh)
        self.stakes_of.setdefault(mid, set()).update(fresh)
        for start in range(0, len(fresh), 40):
            req = self.pb.MainRequest()
            req.stakes_subscribe.uid = self.uid("stakes")
            for stake_id in fresh[start:start + 40]:
                item = req.stakes_subscribe.stakes.add()
                item.uid = self.uid("k")
                item.match_id = mid
                item.stake_id = stake_id
            await self._send(ws, req, tag=f"subscribe_stakes:{mid}")

    def _now(self) -> float:
        return self.clock.now().wall_ns / 1e9

    def _is_over(self, match) -> bool:
        return match.info.match_status.type == self.pb.MATCH_STATUSES_FINISHED

    def _touch(self, mid: int) -> None:
        """Price activity on a subscribed match: its slot is earning its keep."""
        if mid in self.subscribed:
            self.seen_at[mid] = self._now()

    async def _finish(self, ws, mid: int, why: str) -> None:
        """The feed says this match is over (or it is not wanted): never again."""
        if not mid:
            return
        self.done.add(mid)
        if mid in self.subscribed:
            await self._release(ws, mid, why)

    async def _release(self, ws, mid: int, why: str) -> None:
        """Give a slot back and tell the server we are done with the match.

        Its followed outcomes are released too, so a session that runs for
        days does not pile up subscriptions the server keeps for nobody. Both
        requests are best effort: their replies go through _check_code like
        every other, so a refusal is reported, not swallowed.
        """
        self.subscribed.discard(mid)
        self.seen_at.pop(mid, None)
        stakes = sorted(self.stakes_of.pop(mid, set()))
        self.stakes_asked.difference_update(stakes)
        self.released[why] += 1
        print(f"[unsub] match {mid}: {why}", file=sys.stderr)
        req = self.pb.MainRequest()
        req.matches_unsubscribe_full.uid = self.uid("unfull")
        item = req.matches_unsubscribe_full.full_matches.add()
        item.uid = self.uid("u")
        item.match_id = mid
        await self._send(ws, req, tag=f"unsubscribe_full:{mid}")
        for start in range(0, len(stakes), 40):
            req = self.pb.MainRequest()
            req.stakes_unsubscribe.uid = self.uid("unstakes")
            for stake_id in stakes[start:start + 40]:
                it = req.stakes_unsubscribe.stakes.add()
                it.uid = self.uid("uk")
                it.match_id = mid
                it.stake_id = stake_id
            await self._send(ws, req, tag=f"unsubscribe_stakes:{mid}")

    async def _release_stale(self, ws, now: float | None = None) -> list[int]:
        """Release every subscribed match that has priced nothing for STALE_MATCH_S."""
        now = self._now() if now is None else now
        stale = sorted(m for m in self.subscribed
                       if now - self.seen_at.get(m, now) > STALE_MATCH_S)
        for mid in stale:
            self.cooldown[mid] = now + STALE_COOLDOWN_S
            await self._release(ws, mid, "stale")
        return stale

    async def _release_loop(self, ws, every: float = 60.0) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                await self._release_stale(ws)
            except Exception:
                return                      # the socket is gone; the next session starts its own

    def _note_match(self, match) -> None:
        for stake in getattr(match, "stakes", []):
            self._note_stake(stake)

    def _note_stake(self, stake) -> None:
        self.stakes_seen += 1
        self.last_stake_at = self.clock.now().wall_ns / 1e9
        self.markets[(stake.market_name, stake.period_name)] += 1
        if self.discover and self.stakes_seen % 200 == 0:
            self.report()

    def report(self) -> None:
        print(f"\n--- {self.stakes_seen} stake updates, "
              f"{len(self.markets)} (market, period) pairs ---", file=sys.stderr)
        for (market, period), n in self.markets.most_common(40):
            print(f"  {n:6d}  {market} | {period}", file=sys.stderr)

        print(f"\n--- responses by type ---", file=sys.stderr)
        if not self.kinds:
            print("  none. The socket carried frames but no parseable "
                  "MainResponse, or none arrived at all.", file=sys.stderr)
        for kind, n in self.kinds.most_common():
            print(f"  {n:6d}  {kind}", file=sys.stderr)

        if self.sports_seen:
            print(f"\n--- sports in the tree (name, tournaments, matches) ---",
                  file=sys.stderr)
            for (name, n_t, n_m), n in self.sports_seen.most_common(30):
                print(f"  {n:4d}x  {name!r:34s} tournaments={n_t:4d} "
                      f"matches={n_m:5d}", file=sys.stderr)
        else:
            print("\n--- no sports tree arrived ---", file=sys.stderr)

        if self.errors:
            print(f"\n--- {len(self.errors)} server error(s) ---",
                  file=sys.stderr)
            for text in self.errors[:10]:
                print(f"  {text}", file=sys.stderr)

        if self.bad_codes:
            print(f"\n--- {len(self.bad_codes)} refused request(s) ---",
                  file=sys.stderr)
            for text in self.bad_codes[:10]:
                print(f"  {text}", file=sys.stderr)

        if self.skipped:
            by_reason = Counter()
            for (reason, _), n in self.skipped.items():
                by_reason[reason] += 1
            print(f"\n--- tournaments not subscribed: "
                  f"{', '.join(f'{r}={n}' for r, n in by_reason.items())} ---",
                  file=sys.stderr)
            for (reason, name), _ in sorted(self.skipped.items())[:12]:
                print(f"  {reason:10s} {name}", file=sys.stderr)

        print(f"\n  layers asked: sports={sorted(self.sports_asked)} "
              f"categories={len(self.categories_asked)} "
              f"tournaments={len(self.tournaments_asked)}", file=sys.stderr)

        print(f"\n  following {len(self.stakes_asked)} game outcome(s) one by one; "
              f"{self.stake_pushes} per-stake push(es) arrived",
              file=sys.stderr)
        if self.stakes_asked and not self.stake_pushes:
            print("  none arrived -- prices inside a game were NOT recorded",
                  file=sys.stderr)

        print(f"\n  subscribed to {len(self.subscribed)} match(es): "
              f"{sorted(self.subscribed)}", file=sys.stderr)
        for mid in sorted(self.subscribed):
            cat, tour = self.match_tier.get(mid, ("?", "?"))
            print(f"    {mid}  [{cat}] {tour}", file=sys.stderr)


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
    ap.add_argument("--include-doubles", action="store_true",
                    help="also subscribe to doubles tournaments (skipped by "
                         "default: singles' serve model does not apply)")
    ap.add_argument("--include-sim", action="store_true",
                    help="also subscribe to simulator 'tennis' such as "
                         "ESportsBattle eTennis (skipped by default: not tennis)")
    ap.add_argument("--no-compress", action="store_true",
                    help="write plain .jsonl instead of .jsonl.gz. Costs about "
                         "5x the disk (measured); use it only to read a capture "
                         "by eye")
    ap.add_argument("--time-filter", default="",
                    help="value for settings_set.time_filter. Left empty the "
                         "call is skipped, because sending it without one is "
                         "refused (code 400, violation on time_filter) and the "
                         "tree works without it")
    args = ap.parse_args(argv)

    with RawLog(args.out, provider="betboom", compress=not args.no_compress) as log:
        rec = BetBoomRecorder(log, url=args.url, max_matches=args.max_matches,
                              sport=args.sport, discover=args.discover,
                              time_filter=args.time_filter)
        rec.include_doubles = args.include_doubles
        rec.include_sim = args.include_sim
        try:
            asyncio.run(rec.run())
        except KeyboardInterrupt:
            pass
        finally:
            rec.report()
            rec._remove_sidecar()
            print(f"\n{log.frames} frames, {log.bytes} bytes -> {log.root}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
