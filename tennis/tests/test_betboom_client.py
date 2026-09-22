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
from tennis.ingest.rawlog import RawLog, find_logs, read_raw

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
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
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


def test_table_tennis_is_not_tennis(tmp_path):
    """Setka Cup filled every slot on the first end-to-end run."""
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_sports.states.add()
    sport = state.sports.add()
    sport.info.id = 26
    sport.info.name = "Настольный теннис"
    sport.info.url_slug = "table-tennis"
    sport.info.matches_count = 21
    tour = sport.tournaments.add()
    tour.info.name = "Setka Cup"
    tour.matches.add().info.id = 5962314
    rec, ws, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.subscribed == set()
    assert rec.sports_asked == set()


def test_every_frame_is_logged_raw_before_parsing(tmp_path):
    frames = [tennis_tree(1), stake_push("Точный счёт гейма", "1-й сет, 6-й гейм", 4.3),
              b"\x00\xffnot-a-valid-message"]
    rec, _, log = run_session(tmp_path, frames)
    path = find_logs(tmp_path)[0]
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


def test_lazy_sports_layer_triggers_a_sport_subscription(tmp_path):
    """The bug that made the first live session silent.

    And the second one: the first fix asked state_subscribe_by_categories,
    which the live server refuses for anything but esports (sport_id must be
    1). A real sport is subscribed with state_subscribe_sports.
    """
    rec, ws, _ = run_session(tmp_path, [lazy_sports_tree()])
    reqs = []
    for raw in ws.sent:
        r = pb.MainRequest(); r.ParseFromString(raw)
        reqs.append(r)
    kinds = [r.WhichOneof("type") for r in reqs]
    assert "state_subscribe_by_categories" not in kinds
    assert "state_subscribe_sports" in kinds
    sub = next(r for r in reqs if r.WhichOneof("type") == "state_subscribe_sports")
    item = sub.state_subscribe_sports.sports[0]
    assert item.sport_id == 4
    assert item.type == pb.TREE_TYPES_LIVE
    assert rec.sports_asked == {4}


def test_sport_subscription_reply_carries_the_tree(tmp_path):
    """The reply to state_subscribe_sports is the tree itself (~100 KB live)."""
    msg = pb.MainResponse()
    msg.state_subscribe_sports.code = 200
    item = msg.state_subscribe_sports.sports.add()
    item.code = 200
    item.sport.info.id = 4
    item.sport.info.name = "Теннис"
    item.sport.info.url_slug = "tennis"
    tour = item.sport.tournaments.add()
    tour.info.id = 38997
    tour.info.name = "WTA 125. Анкара. Хард. Турция"
    for mid in (5961569, 5950399):
        tour.matches.add().info.id = mid
    rec, ws, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.subscribed == {5961569, 5950399}


def test_full_reply_records_the_tier(tmp_path, capsys):
    msg = pb.MainResponse()
    item = msg.matches_subscribe_full.full_matches.add()
    item.code = 200
    item.match.info.id = 5961569
    item.category.info.name = "WTA"
    item.tournament.info.name = "WTA 125. Анкара. Хард. Турция"
    rec, _, _ = run_session(tmp_path, [msg.SerializeToString()])
    assert rec.match_tier[5961569] == ("WTA", "WTA 125. Анкара. Хард. Турция")


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
    assert kinds.count("state_subscribe_sports") == 1


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


def test_reconnect_resubscribes_from_scratch(tmp_path):
    """After a dropped socket every layer must be asked for again.

    Observed on the first long run: one ConnectionClosedError, a successful
    reconnect, and then hours of a growing log with no prices in it. The
    reconnect path cleared `subscribed` but not the per-layer memo sets, so
    the sport was treated as already requested and nothing below it was ever
    asked for again.
    """
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
    log.open()
    rec = BetBoomRecorder(log)

    ws1 = FakeWS([lazy_sports_tree()])
    asyncio.run(rec._session(ws1))
    assert rec.sports_asked == {4}
    first = [pb.MainRequest.FromString(r).WhichOneof("type") for r in ws1.sent]
    assert first.count("state_subscribe_sports") == 1

    # the socket drops; this is what the reconnect loop does before dialling
    rec._forget_subscriptions()
    assert rec.sports_asked == set() and rec.subscribed == set()

    ws2 = FakeWS([lazy_sports_tree()])
    asyncio.run(rec._session(ws2))
    second = [pb.MainRequest.FromString(r).WhichOneof("type") for r in ws2.sent]
    assert second.count("state_subscribe_sports") == 1, \
        "the sport was not re-requested on the new socket"
    log.close()


def test_recorder_publishes_its_counters_beside_the_log(tmp_path):
    """The sidecar is what lets an outside checker tell 'writing' from
    'writing what we came for'."""
    import json
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
    log.open()
    rec = BetBoomRecorder(log)
    asyncio.run(rec._session(FakeWS([tennis_tree(555),
                                     stake_push("Исход", "", 1.8)])))
    rec._write_sidecar()
    side = json.loads((tmp_path / "_recorder.json").read_text())
    assert side["subscribed"] == 1
    assert side["stakes_seen"] == 1
    assert side["last_stake_at_s"] is not None
    assert side["reconnects"] == 0
    assert side["frames"] == log.frames
    assert not list(tmp_path.glob("*.tmp"))
    log.close()


def _tree_with(tournaments):
    """A sport-subscription reply carrying the given (category, name, match_id)s."""
    msg = pb.MainResponse()
    msg.state_subscribe_sports.code = 200
    item = msg.state_subscribe_sports.sports.add()
    item.code = 200
    item.sport.info.id = 4
    item.sport.info.name = "Теннис"
    item.sport.info.url_slug = "tennis"
    for i, (cat, name, mid) in enumerate(tournaments):
        t = item.sport.tournaments.add()
        t.info.id = 1000 + i
        t.info.name = name
        t.category.info.name = cat
        t.matches.add().info.id = mid
    return msg.SerializeToString()


LIVE_TREE = [
    ("WTA", "WTA 125. Анкара. Хард. Турция", 1),
    ("WTA", "WTA 125. Анкара. Хард. Пары", 2),
    ("Кибертеннис", "ESportsBattle eTennis ATP Championship", 3),
    ("Challenger", "ATP Challenger. Генуя. Грунт. Италия", 4),
    ("WTT", "WTT 25. Сетубал. Хард. Пары", 5),
]


def test_doubles_and_simulators_do_not_take_subscription_slots(tmp_path):
    """Measured live: 12 of 32 tennis tournaments were doubles or a simulator.

    With --max-matches 10 taking the tree in order, those ate slots meant for
    singles. They are now skipped at subscription time only -- whatever the
    feed pushes is still recorded raw.
    """
    rec, _, _ = run_session(tmp_path, [_tree_with(LIVE_TREE)])
    assert rec.subscribed == {1, 4}
    reasons = {r for (r, _) in rec.skipped}
    assert reasons == {"doubles", "simulator"}
    assert sum(rec.skipped.values()) == 3


def test_skips_are_opt_out(tmp_path):
    log = RawLog(tmp_path, provider="betboom", clock=FakeClock(), compress=False)
    log.open()
    rec = BetBoomRecorder(log)
    rec.include_doubles = True
    rec.include_sim = True
    asyncio.run(rec._session(FakeWS([_tree_with(LIVE_TREE)])))
    assert rec.subscribed == {1, 2, 3, 4, 5}
    assert not rec.skipped
    log.close()


def test_skip_report_names_the_tournaments(tmp_path, capsys):
    rec, _, _ = run_session(tmp_path, [_tree_with(LIVE_TREE)])
    rec.report()
    err = capsys.readouterr().err
    assert "tournaments not subscribed" in err
    assert "ESportsBattle" in err and "Пары" in err
