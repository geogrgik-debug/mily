"""Subscription slots come back when a match is over.

The night of 22-23.09 on the capture host: ten matches subscribed at 21:33
MSK, prices at ~50 000 per quarter hour, then fading as those matches ended,
and from 01:35 nothing -- for six and a half hours, "10 subscribed" all along,
the log growing on the tour-wide score stream. `subscribed` only ever grew and
the cap turned every later match away. The hourly watchdog caught it as
"writing idle". These tests pin the way out: the feed's DELETE and FINISHED
free a slot, silence frees it as a backstop, and the next live match takes it.
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
    STALE_COOLDOWN_S, STALE_MATCH_S, BetBoomRecorder)

GAME = "1-й сет 3-й гейм: Исход"


class FakeWS:
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


@pytest.fixture
def rig(tmp_path):
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
    log.open()
    clock = FakeClock()
    yield log, clock
    log.close()


def recorder(rig, **kw):
    log, clock = rig
    return BetBoomRecorder(log, clock=clock, **kw), clock


def feed(rec, frames) -> FakeWS:
    ws = FakeWS(frames)
    asyncio.run(rec._session(ws))
    return ws


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


def match_push(mid, *, finished=False, delete=False, tournament_id=10):
    msg = pb.MainResponse()
    n = msg.newsletters_match
    n.action = pb.NEWSLETTER_ACTIONS_DELETE if delete else pb.NEWSLETTER_ACTIONS_UPDATE
    n.match.info.id = mid
    n.match.info.tournament_id = tournament_id
    if finished:
        n.match.info.match_status.type = pb.MATCH_STATUSES_FINISHED
    return msg.SerializeToString()


def board(mid, stakes=(), *, finished=False, delete=False):
    msg = pb.MainResponse()
    n = msg.newsletters_full_match
    n.action = pb.NEWSLETTER_ACTIONS_DELETE if delete else pb.NEWSLETTER_ACTIONS_UPDATE
    n.match.info.id = mid
    if finished:
        n.match.info.match_status.type = pb.MATCH_STATUSES_FINISHED
    for sid, market in stakes:
        s = n.match.stakes.add()
        s.stake_id = sid
        s.market_name = market
        s.is_active = True
    return msg.SerializeToString()


def test_the_night_of_22_09_ten_finish_and_ten_new_ones_are_taken(rig):
    rec, _ = recorder(rig, max_matches=10)
    evening, night = list(range(100, 110)), list(range(200, 210))
    feed(rec, [tree(evening)] + [match_push(m) for m in night])
    assert rec.subscribed == set(evening)          # the cap holds while they play

    ws = feed(rec, [match_push(m, finished=True) for m in evening]
                   + [match_push(m) for m in night])
    assert rec.subscribed == set(night)
    unsubscribed = sorted(u.full_matches[0].match_id
                          for u in sent(ws, "matches_unsubscribe_full"))
    assert unsubscribed == evening
    assert rec.released == {"finished": 10}


def test_a_match_deleted_from_the_live_tree_gives_its_slot_back(rig):
    rec, _ = recorder(rig, max_matches=1)
    feed(rec, [tree([1])])
    feed(rec, [match_push(1, delete=True), match_push(2)])
    assert rec.subscribed == {2}
    assert rec.released == {"deleted": 1}


def test_a_board_marked_finished_gives_its_slot_back(rig):
    rec, _ = recorder(rig, max_matches=2)
    feed(rec, [tree([1, 2])])
    feed(rec, [board(1, finished=True)])
    assert rec.subscribed == {2} and 1 in rec.done


def test_a_deleted_board_gives_its_slot_back(rig):
    rec, _ = recorder(rig, max_matches=2)
    feed(rec, [tree([1, 2])])
    feed(rec, [board(2, delete=True)])
    assert rec.subscribed == {1}
    assert rec.released == {"deleted": 1}


def test_a_finished_match_is_never_taken_again_even_after_a_reconnect(rig):
    rec, _ = recorder(rig, max_matches=3)
    feed(rec, [tree([1])])
    feed(rec, [match_push(1, finished=True), match_push(1)])
    assert rec.subscribed == set()
    rec._forget_subscriptions()                 # the socket drops
    feed(rec, [tree([1, 2])])
    assert rec.subscribed == {2}


def test_a_silent_match_is_released_after_twenty_minutes_and_cools_down(rig):
    rec, clock = recorder(rig, max_matches=2)
    ws = feed(rec, [tree([1, 2])])
    clock.advance(STALE_MATCH_S - 60)
    feed(rec, [board(2)])                       # 2 is pricing; 1 has said nothing
    clock.advance(120)
    assert asyncio.run(rec._release_stale(ws)) == [1]
    assert rec.subscribed == {2}
    assert rec.released == {"stale": 1}

    feed(rec, [match_push(1)])                  # it reappears at once...
    assert 1 not in rec.subscribed              # ...and waits out the cooldown
    clock.advance(STALE_COOLDOWN_S + 1)
    feed(rec, [match_push(1)])
    assert rec.subscribed == {1, 2}


def test_any_price_activity_keeps_a_slot(rig):
    rec, clock = recorder(rig, max_matches=1)
    ws = feed(rec, [tree([1])])
    push = pb.MainResponse()
    push.newsletters_stake.action = pb.NEWSLETTER_ACTIONS_UPDATE
    push.newsletters_stake.stake.match_id = 1
    push.newsletters_stake.stake.market_name = GAME
    for _ in range(3):
        clock.advance(STALE_MATCH_S - 60)
        feed(rec, [push.SerializeToString()])   # a per-outcome push, no board
    assert asyncio.run(rec._release_stale(ws)) == []
    assert rec.subscribed == {1}


def test_releasing_a_match_lets_its_followed_outcomes_go(rig):
    rec, _ = recorder(rig, max_matches=1)
    feed(rec, [tree([1]), board(1, [("g1", GAME), ("g2", "1-й сет 3-й гейм: Точный счёт")])])
    assert rec.stakes_asked == {"g1", "g2"}
    ws = feed(rec, [match_push(1, finished=True)])
    unsub = sent(ws, "stakes_unsubscribe")
    assert [(s.match_id, s.stake_id) for s in unsub[0].stakes] == [(1, "g1"), (1, "g2")]
    assert rec.stakes_asked == set()


def test_doubles_from_the_stream_do_not_take_a_freed_slot(rig):
    rec, _ = recorder(rig, max_matches=1)
    feed(rec, [tree([1]), tree([50], tournament_id=77, name="WTT 25. Сетубал. Хард. Пары")])
    assert 77 in rec.unwanted_tournaments
    feed(rec, [match_push(1, finished=True),
               match_push(50, tournament_id=77),     # doubles: turned away
               match_push(60, tournament_id=10)])
    assert rec.subscribed == {60}


def test_a_match_whose_reply_names_an_unwanted_tournament_is_let_go(rig):
    rec, _ = recorder(rig, max_matches=2)
    feed(rec, [match_push(5, tournament_id=0)])      # tournament not seen yet: taken
    assert rec.subscribed == {5}
    reply = pb.MainResponse()
    item = reply.matches_subscribe_full.full_matches.add()
    item.code = 200
    item.match.info.id = 5
    item.tournament.info.id = 88
    item.tournament.info.name = "ITF. Анталья. Грунт. Пары"
    item.category.info.name = "ITF"
    feed(rec, [reply.SerializeToString()])
    assert rec.subscribed == set() and 5 in rec.done
    assert 88 in rec.unwanted_tournaments
    assert rec.released == {"doubles": 1}


def test_the_sidecar_reports_the_releases(rig, tmp_path):
    rec, _ = recorder(rig, max_matches=1)
    feed(rec, [tree([1]), match_push(1, finished=True)])
    rec._write_sidecar()
    data = json.loads(rec.sidecar_path().read_text(encoding="utf-8"))
    assert data["released"] == {"finished": 1}
    assert data["subscribed"] == 0
