"""Drive the BetBoom recorder against a fake socket.

The live socket cannot be reached from CI, but everything except the network
can still be pinned down: that we speak the recovered schema, that a tennis
match in the tree gets a full-market subscription, that the concurrency cap
holds, and that every frame reaches the raw log before it is parsed.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog, read_raw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest/betboom/generated"))

pb = pytest.importorskip(
    "bb_sport_ws_v1_pb2",
    reason="generated protobuf classes missing; build them with grpc_tools.protoc "
           "(see tennis/ingest/betboom/README.md)",
)

from tennis.ingest.betboom.client import BetBoomRecorder  # noqa: E402


def tennis_tree(match_id: int = 555, n_matches: int = 1) -> bytes:
    """A LIVE tree response carrying a tennis tournament."""
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_sports.states.add()
    state.type = pb.TREE_TYPES_LIVE
    sport = state.sports.add()
    sport.info.id = 2
    sport.info.name = "Теннис"
    sport.info.url_slug = "tennis"
    tour = sport.tournaments.add()
    tour.info.name = "Challenger Ningbo"
    for i in range(n_matches):
        m = tour.matches.add()
        m.info.id = match_id + i
        m.info.is_active = True
    return msg.SerializeToString()


def stake_push(market: str, period: str, factor: float) -> bytes:
    msg = pb.MainResponse()
    n = msg.newsletters_stake
    n.action = pb.NEWSLETTER_ACTIONS_UPDATE
    n.stake.market_name = market
    n.stake.period_name = period
    n.stake.factor = factor
    n.stake.is_active = True
    return msg.SerializeToString()


class FakeWS:
    """Yields canned frames, collects what the recorder sends."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        async def gen():
            for f in self._frames:
                yield f
        return gen()


def run_session(tmp_path, frames, **kw):
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock())
    log.open()
    rec = BetBoomRecorder(log, **kw)
    ws = FakeWS(frames)
    asyncio.run(rec._session(ws))
    log.close()
    return rec, ws, log


def test_subscribes_to_tennis_match_with_full_markets(tmp_path):
    rec, ws, _ = run_session(tmp_path, [tennis_tree(555)])
    reqs = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        reqs.append(r)
    kinds = [r.WhichOneof("type") for r in reqs]
    assert kinds[0] == "settings_set"
    assert kinds[1] == "state_subscribe_by_sports"
    assert "matches_subscribe_full" in kinds
    full = next(r for r in reqs if r.WhichOneof("type") == "matches_subscribe_full")
    # one match per request, as the client's own config caps it
    assert len(full.matches_subscribe_full.full_matches) == 1
    assert full.matches_subscribe_full.full_matches[0].match_id == 555
    assert rec.subscribed == {555}


def test_concurrency_cap_is_respected(tmp_path):
    rec, _, _ = run_session(tmp_path, [tennis_tree(100, n_matches=20)], max_matches=3)
    assert len(rec.subscribed) == 3


def test_non_tennis_sport_is_ignored(tmp_path):
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_sports.states.add()
    sport = state.sports.add()
    sport.info.name = "Футбол"; sport.info.url_slug = "football"
    tour = sport.tournaments.add(); tour.matches.add().info.id = 9
    rec, _, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.subscribed == set()


def test_every_frame_is_logged_raw_before_parsing(tmp_path):
    frames = [tennis_tree(1), stake_push("Точный счёт гейма", "1-й сет, 6-й гейм", 4.3),
              b"\x00\xffnot-a-valid-message"]
    rec, _, log = run_session(tmp_path, frames)
    path = next(Path(tmp_path).rglob("*.jsonl"))
    rx = [r["payload"] for r in read_raw(path) if r["dir"] == "rx"]
    assert rx == frames            # including the frame that failed to parse
    assert rec.stakes_seen == 1
    assert rec.markets[("Точный счёт гейма", "1-й сет, 6-й гейм")] == 1


def test_garbage_frame_does_not_kill_the_session(tmp_path):
    frames = [b"\x00\xffgarbage", tennis_tree(7)]
    rec, _, _ = run_session(tmp_path, frames)
    assert rec.subscribed == {7}   # recovered and kept going
