"""What the recorder does between sockets.

23.09 from about 11:37 to 11:51 MSK the feed accepted every connection and
closed it at once with 3010 "Access rejected", on its side -- a second address
got the same. The capture host reconnected 561 times in that quarter of an
hour: the wait before the next attempt was reset on every handshake, so a
refusal right after one never backed off. Each reconnect also left its per-
session loops running. These tests pin the way it should behave instead.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest/betboom/generated"))

pb = pytest.importorskip(
    "bb_sport_ws_v1_pb2",
    reason="generated protobuf classes missing; build them with grpc_tools.protoc "
           "(see tennis/ingest/betboom/README.md)",
)

from tennis.ingest.betboom.client import (  # noqa: E402
    HEALTHY_SESSION_S, MAX_BACKOFF_S, BetBoomRecorder)

REJECTED = "received 3010 (registered) Access rejected; then sent 3010 (registered) Access rejected"


class Rejected(Exception):
    """Stands in for websockets' ConnectionClosedError."""


class Stop(BaseException):
    """Ends run(): not an Exception, so the reconnect loop does not catch it."""


class FakeWS:
    """A socket that delivers its frames, then closes: cleanly, or with `error`.

    `lasts` is how long the session runs on the recorder's clock before closing.
    """

    def __init__(self, frames=(), *, error=None, lasts=0.0, clock=None):
        self.frames = list(frames)
        self.error = error
        self.lasts = lasts
        self.clock = clock
        self.sent: list[bytes] = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        async def gen():
            for f in self.frames:
                yield f
            if self.lasts:
                self.clock.advance(self.lasts)
            if self.error:
                raise self.error
        return gen()


class Server:
    """Hands out the scripted sockets one per connection, then stops the run."""

    def __init__(self, sockets):
        self.sockets = list(sockets)
        self.served: list[FakeWS] = []

    def connect(self):
        server = self

        class Conn:
            async def __aenter__(self):
                if not server.sockets:
                    raise Stop
                ws = server.sockets.pop(0)
                if isinstance(ws, Exception):       # the handshake itself failed
                    raise ws
                server.served.append(ws)
                return ws

            async def __aexit__(self, *exc):
                return False

        return Conn()


@pytest.fixture
def rig(tmp_path):
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
    log.open()
    clock = FakeClock()
    rec = BetBoomRecorder(log, clock=clock, max_matches=10)
    waits, alive = [], []

    async def sleep(seconds):
        waits.append(seconds)
        clock.advance(seconds)
        for _ in range(3):                  # let cancelled tasks finish
            await asyncio.sleep(0)
        alive.append(sum(not t.done() for t in asyncio.all_tasks()))

    rec._sleep = sleep
    yield rec, clock, waits, alive
    log.close()


def run(rec, sockets):
    server = Server(sockets)
    with pytest.raises(Stop):
        asyncio.run(rec.run(connect=server.connect))
    return server


def refused():
    return FakeWS(error=Rejected(REJECTED))


def sent(ws, kind):
    out = []
    for raw in ws.sent:
        req = pb.MainRequest()
        req.ParseFromString(raw)
        if req.WhichOneof("type") == kind:
            out.append(getattr(req, kind))
    return out


def tree(ids, tournament_id=10, name="Challenger Ningbo"):
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_sports.states.add()
    state.type = pb.TREE_TYPES_LIVE
    sport = state.sports.add()
    sport.info.id = 4
    sport.info.name = "Теннис"
    sport.info.url_slug = "tennis"
    tour = sport.tournaments.add()
    tour.info.id = tournament_id
    tour.info.name = name
    for mid in ids:
        m = tour.matches.add()
        m.info.id = mid
        m.info.is_active = True
        m.info.tournament_id = tournament_id
    return msg.SerializeToString()


def test_a_server_that_refuses_every_session_is_backed_off_not_hammered(rig):
    rec, _, waits, _ = rig
    run(rec, [refused() for _ in range(20)])
    assert waits[:8] == [1, 2, 4, 8, 16, 32, MAX_BACKOFF_S, MAX_BACKOFF_S]
    assert rec.reconnects == 20
    # Twenty refusals now span a quarter of an hour; on 23.09 it took 561.
    assert sum(waits) >= 15 * 60


def test_a_session_that_lived_is_retried_at_once_then_backed_off(rig):
    rec, clock, waits, _ = rig
    run(rec, [refused(), refused(),
              FakeWS(error=Rejected("keepalive ping timeout"),
                     lasts=HEALTHY_SESSION_S, clock=clock),
              refused(), refused()])
    assert waits == [1, 2, 0, 1, 2]


def board(mid, stake_id="s11"):
    msg = pb.MainResponse()
    n = msg.newsletters_full_match
    n.action = pb.NEWSLETTER_ACTIONS_UPDATE
    n.match.info.id = mid
    s = n.match.stakes.add()
    s.stake_id = stake_id
    s.market_name = "1-й сет 3-й гейм: Исход"
    s.is_active = True
    return msg.SerializeToString()


def dropped():
    """23.09 from 15:20 MSK, on the capture host and a second address alike."""
    err = Rejected("no close frame received or sent")
    err.__cause__ = ConnectionResetError(104, "Connection reset by peer")
    return err


def test_a_drop_names_what_caused_it(rig):
    rec, *_ = rig
    run(rec, [FakeWS(error=dropped())])
    assert rec.last_disconnect == ("Rejected: no close frame received or sent "
                                   "<- ConnectionResetError: [Errno 104] Connection reset by peer")


def test_blind_time_runs_from_the_last_frame_to_the_first_price_after(rig):
    rec, *_ = rig
    run(rec, [FakeWS([tree([1]), board(1)], error=dropped()),   # prices, then gone
              dropped(),                                        # handshake fails: +1 s
              FakeWS([tree([1]), board(1)])])                   # +2 s: prices again
    assert rec.last_blind_s == 3.0 and rec.blind_s == 3.0
    # The last socket closed too, and its gap is still open: 4 s so far.
    rec._write_sidecar()
    data = json.loads(rec.sidecar_path().read_text(encoding="utf-8"))
    assert data["blind_s"] == 7.0 and data["last_blind_s"] == 3.0


def test_a_wall_clock_step_inside_a_gap_does_not_bend_it(rig):
    rec, clock, *_ = rig
    plain = rec._sleep

    async def ntp_step(seconds):
        clock.advance(0, wall_step=-3600)
        await plain(seconds)

    rec._sleep = ntp_step
    run(rec, [FakeWS([tree([1]), board(1)], error=dropped()),
              FakeWS([tree([1]), board(1)])])
    assert rec.last_blind_s == 1.0


def test_a_drop_with_nothing_subscribed_costs_nothing(rig):
    rec, *_ = rig
    run(rec, [refused(), FakeWS([tree([1]), board(1)], error=dropped())])
    assert rec.last_blind_s is None and rec.blind_s == 0.0


def test_the_sidecar_lists_the_last_ten_drops(rig):
    rec, *_ = rig
    run(rec, [dropped()] + [refused() for _ in range(11)])
    rec._write_sidecar()
    drops = json.loads(rec.sidecar_path().read_text(encoding="utf-8"))["disconnects"]
    assert len(drops) == 10 and rec.reconnects == 12
    assert all("3010" in d["why"] and d["lived_s"] == 0.0 for d in drops)
    run(rec, [dropped()])
    assert rec.disconnects[-1]["lived_s"] is None         # never got past the handshake


def test_a_clean_close_forgets_the_subscriptions_and_asks_again(rig):
    """1000/1001 end the frame loop without raising -- the shape of a server
    restart. The recorder used to go straight back in remembering its matches,
    so the new socket was asked for nothing: prices stopped, "10 subscribed"."""
    rec, _, waits, _ = rig
    server = run(rec, [FakeWS([tree([1, 2])]), FakeWS([tree([1, 2])])])
    first, second = server.served
    asked = lambda ws: sorted(r.full_matches[0].match_id
                              for r in sent(ws, "matches_subscribe_full"))
    assert asked(first) == [1, 2]
    assert asked(second) == [1, 2]
    assert rec.reconnects == 2 and waits == [1, 2]
    assert rec.last_disconnect == "closed by the server"


def test_a_sessions_loops_end_with_it(rig):
    rec, _, _, alive = rig
    run(rec, [refused() for _ in range(6)])
    # The run itself and one heartbeat, however many sockets came and went.
    assert alive == [2] * 6


def test_the_sidecar_says_why_the_socket_last_closed(rig):
    rec, *_ = rig
    run(rec, [refused()])
    rec._write_sidecar()
    data = json.loads(rec.sidecar_path().read_text(encoding="utf-8"))
    assert "3010" in data["last_disconnect"] and "Access rejected" in data["last_disconnect"]
    assert data["reconnects"] == 1 and data["subscribed"] == 0
