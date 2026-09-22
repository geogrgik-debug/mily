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
    # settings_set is no longer sent: the live server refuses it with code 400
    # and a violation on `time_filter`, and the tree works without it.
    assert "settings_set" not in kinds
    assert kinds[0] == "state_subscribe_by_sports"
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


def test_settings_set_is_sent_only_with_a_time_filter(tmp_path):
    """The one value the schema does not supply has to come from the caller."""
    _, ws, _ = run_session(tmp_path, [], time_filter="all")
    kinds = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        kinds.append(r.WhichOneof("type"))
    assert kinds[0] == "settings_set"
    first = pb.MainRequest(); first.ParseFromString(ws.sent[0])
    assert first.settings_set.time_filter == "all"


def lazy_sports_tree() -> bytes:
    """The sports layer as the live server actually sends it: counts, no children.

    Measured 2026-09-22: 16 sports, tennis id=4 with matches_count=62 and
    tournaments_count=28, and not one tournament inline.
    """
    msg = pb.MainResponse()
    msg.state_subscribe_by_sports.code = 200
    state = msg.state_subscribe_by_sports.states.add()
    state.type = pb.TREE_TYPES_LIVE
    sport = state.sports.add()
    sport.info.id = 4
    sport.info.name = "Теннис"
    sport.info.url_slug = "tennis"
    sport.info.matches_count = 62
    sport.info.tournaments_count = 28
    return msg.SerializeToString()


def test_lazy_sports_layer_triggers_a_category_request(tmp_path):
    """The bug that made the first live session silent."""
    rec, ws, _ = run_session(tmp_path, [lazy_sports_tree()])
    reqs = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        reqs.append(r)
    kinds = [r.WhichOneof("type") for r in reqs]
    assert "state_subscribe_by_categories" in kinds
    cats = next(r for r in reqs
                if r.WhichOneof("type") == "state_subscribe_by_categories")
    assert cats.state_subscribe_by_categories.sport_id == 4
    assert list(cats.state_subscribe_by_categories.types) == [pb.TREE_TYPES_LIVE]
    assert rec.sports_asked == {4}


def test_category_layer_is_asked_only_once_per_sport(tmp_path):
    """The same sport arrives repeatedly as newsletters_sport pushes."""
    push = pb.MainResponse()
    push.newsletters_sport.code = 200
    push.newsletters_sport.sport.info.id = 4
    push.newsletters_sport.sport.info.name = "Теннис"
    push.newsletters_sport.sport.info.url_slug = "tennis"
    push.newsletters_sport.sport.info.tournaments_count = 28
    frame = push.SerializeToString()
    _, ws, _ = run_session(tmp_path, [lazy_sports_tree(), frame, frame, frame])
    kinds = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        kinds.append(r.WhichOneof("type"))
    assert kinds.count("state_subscribe_by_categories") == 1


def test_tennis_arriving_as_a_sport_push_is_taken(tmp_path):
    """Tennis came as newsletters_sport, not inside the subscription reply."""
    push = pb.MainResponse()
    push.newsletters_sport.sport.info.id = 4
    push.newsletters_sport.sport.info.name = "Теннис"
    push.newsletters_sport.sport.info.url_slug = "tennis"
    push.newsletters_sport.sport.info.tournaments_count = 28
    rec, _, _ = run_session(tmp_path, [push.SerializeToString()])
    assert rec.sports_asked == {4}


def test_category_with_no_tournaments_asks_the_next_layer(tmp_path):
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_categories.states.add()
    cat = state.categories.add()
    cat.info.id = 302
    cat.info.sport_id = 4
    cat.info.name = "ATP"
    cat.info.tournaments_count = 3
    rec, ws, _ = run_session(tmp_path, [msg.SerializeToString()])
    reqs = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        reqs.append(r)
    sub = next(r for r in reqs
               if r.WhichOneof("type") == "state_subscribe_categories")
    item = sub.state_subscribe_categories.categories[0]
    assert (item.sport_id, item.category_id) == (4, 302)
    assert rec.categories_asked == {(4, 302)}


def test_tournament_with_no_matches_asks_the_next_layer(tmp_path):
    msg = pb.MainResponse()
    msg.newsletters_tournament.tournament.info.id = 40349
    msg.newsletters_tournament.tournament.info.name = "Hangzhou"
    msg.newsletters_tournament.tournament.info.matches_count = 4
    rec, ws, _ = run_session(tmp_path, [msg.SerializeToString()])
    reqs = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        reqs.append(r)
    sub = next(r for r in reqs
               if r.WhichOneof("type") == "state_subscribe_tournaments")
    assert sub.state_subscribe_tournaments.tournaments[0].tournament_id == 40349
    assert rec.tournaments_asked == {40349}


def test_a_match_push_gets_a_full_subscription(tmp_path):
    msg = pb.MainResponse()
    msg.newsletters_match.match.info.id = 40354739
    rec, _, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.subscribed == {40354739}


def test_a_refused_request_is_reported_not_swallowed(tmp_path, capsys):
    """How the real server rejected settings_set: inside the typed response."""
    msg = pb.MainResponse()
    msg.settings_set.code = 400
    msg.settings_set.status = 2
    msg.settings_set.error.message = "Данные не прошли валидацию"
    details = pb.common_BadRequestErrorDetails()
    v = details.violations.add(); v.reason = "time_filter"; v.message = "Не корректно"
    msg.settings_set.error.details.value = details.SerializeToString()
    rec, _, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.bad_codes and "code=400" in rec.bad_codes[0]
    assert "Данные не прошли валидацию" in rec.bad_codes[0]
    assert "time_filter: Не корректно" in rec.bad_codes[0]
    assert "[bad]" in capsys.readouterr().err


def test_a_two_hundred_response_is_not_reported_as_bad(tmp_path):
    rec, _, _ = run_session(tmp_path, [lazy_sports_tree()])
    assert rec.bad_codes == []
