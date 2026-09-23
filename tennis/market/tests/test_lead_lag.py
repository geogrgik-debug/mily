"""Tests for the lead-lag meter: who moves a price first, by how much, how reliably.

The synthetic feeds are shaped after the 2026-09-22 captures on the owner's
laptop: each match's winner repriced every ten to forty seconds (median gap
between two moves of one book on the tour stream, 19.2 s), a two-way book
pushed outcome by outcome milliseconds apart, and a receive jitter of tens of
milliseconds with a rare stall just over a second -- which is what two
recorders on that laptop showed on 2553 byte-identical frames: |B - A| p90
86 ms, p99 0.92 s, max 1.38 s.
"""
import json
import random
import statistics
import sys
from pathlib import Path

import pytest

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog
from tennis.market.lead_lag import (
    TIE_S,
    MatchedMove,
    _stream_arg,
    compare,
    main,
    pair_moves,
    quantile,
    robust_to_window,
    stability,
    wilson,
)
from tennis.market.overround import normalize_proportional, shin
from tennis.market.streams import (
    ClockMismatch,
    PriceEvent,
    Quote,
    RunClock,
    Stream,
    betboom_quotes,
    check_same_clock,
    clock_offset,
    fold,
    load_stream,
)

GENERATED = Path(__file__).resolve().parents[2] / "ingest" / "betboom" / "generated"
BOOT = 1_789_742_505_831_000_000     # the laptop's wall - mono, all four runs of 22.09
T0 = 1_790_088_100_000_000_000       # 22.09, inside the two recorders' overlap
WINNER = ("match", None, None, "Исход")
MID = 5_961_556


def stamp(t, boot=BOOT):
    """(wall, mono) of a frame received t seconds after T0 on a machine."""
    wall = T0 + round(t * 1e9)
    return wall, wall - boot


def quote(t, outcome, odds, *, match=MID, market=WINNER, active=True, boot=BOOT):
    return Quote(*stamp(t, boot), match, market, outcome, odds, active)


def two_way(p, margin=0.06):
    """A two-way book at probability p, at the feed's 0.01 tick."""
    return round(1 / (p * (1 + margin)), 2), round(1 / ((1 - p) * (1 + margin)), 2)


def repricings(n_matches=6, per_match=40, spacing=19.0, seed=7):
    """(t, match, p): every match's winner repriced every 0.5-2 x `spacing`."""
    rng = random.Random(seed)
    out = []
    for m in range(n_matches):
        p, t = 0.5, rng.uniform(0, spacing)
        for _ in range(per_match):
            p = min(0.9, max(0.1, p + rng.choice((-1, 1)) * rng.uniform(0.01, 0.05)))
            out.append((t, MID + m, p))
            t += rng.uniform(0.5, 2.0) * spacing
    return sorted(out)


def jitter(seed, stall_rate=0.01):
    """Receive delay of one recorder: 0-30 ms, and now and then a ~1.2 s stall."""
    rng = random.Random(seed)

    def delay(_):
        d = rng.uniform(0.0, 0.03)
        if rng.random() < stall_rate:
            d += rng.uniform(1.05, 1.3)
        return d
    return delay


def heard(feed, delay=lambda i: 0.0, boot=BOOT):
    """(arrival, match, outcome, odds) as one recorder heard the feed: each
    repricing's two outcomes in two frames 3 ms apart, `delay(i)` late."""
    frames = []
    for i, (t, match, p) in enumerate(feed):
        o1, o2 = two_way(p)
        d = delay(i)
        frames.append((t + d, match, "П1", o1))
        frames.append((t + d + 0.003, match, "П2", o2))
    return sorted(frames)


def capture(feed, delay=lambda i: 0.0, boot=BOOT):
    return [quote(t, o, x, match=m, boot=boot) for t, m, o, x in heard(feed, delay, boot)]


def stream(label, quotes, **kw):
    return Stream(label, "betboom", "synthetic", fold(quotes, **kw), [])


# --------------------------------------------------------------- the zero test


def test_zero_two_captures_of_one_feed_show_no_lag_and_no_leader():
    """The meter must not invent a lag: one feed heard twice is level.

    Both captures carry the jitter measured between two recorders on one
    laptop, stalls included, drawn independently. Every move finds its twin,
    the lag is milliseconds, and nobody leads.
    """
    feed = repricings()
    rep = compare(stream("A", capture(feed, jitter(1))),
                  stream("B", capture(feed, jitter(2))))

    assert rep.moves_a == rep.moves_b == len(rep.matched) > 200
    lags = rep.lags()
    assert abs(statistics.median(lags)) < 0.01
    assert quantile([abs(x) for x in lags], 0.9) < 0.03
    assert rep.order()["="] > 0.95 * len(rep.matched)
    assert rep.stability().leader is None


def test_zero_identical_captures_are_exactly_level():
    feed = repricings()
    rep = compare(stream("A", capture(feed)), stream("B", capture(feed)))
    assert rep.matched and set(rep.lags()) == {0.0}
    assert rep.order() == {"a": 0, "=": len(rep.matched), "b": 0}


def test_zero_end_to_end_two_recorders_on_one_machine(tmp_path):
    """The zero test through everything real: two RawLogs, two processes'
    worth of clocks on one machine, the clock check, the fold, the pairing."""
    feed = repricings()
    record(tmp_path / "a", heard(feed, jitter(1)))
    record(tmp_path / "b", heard(feed, jitter(2)))
    a = load_stream("A", tmp_path / "a", decoder=json_quotes)
    b = load_stream("B", tmp_path / "b", decoder=json_quotes)

    assert check_same_clock([a, b])[("A", "B")] == pytest.approx(0.0, abs=1e-6)
    rep = compare(a, b)
    assert rep.moves_a == rep.moves_b == len(rep.matched) > 200
    assert quantile([abs(x) for x in rep.lags()], 0.9) < 0.03
    assert rep.stability().leader is None


def test_without_a_tie_band_receive_jitter_alone_crowns_a_leader():
    """Why `TIE_S` exists. On 22.09 one recorder was first on 64% of identical
    frames: two processes on one machine do not hear alike. Here B hears
    everything 20 ms after A -- the same feed -- and with no tie band that is a
    perfectly stable leader. With the measured band it is level, as it is."""
    feed = repricings()
    a = stream("A", capture(feed, lambda i: 0.010))
    b = stream("B", capture(feed, lambda i: 0.030))

    assert compare(a, b, tie_s=0.0).stability().stable
    level = compare(a, b)
    assert level.order()["="] == len(level.matched)
    assert level.stability().leader is None


# --------------------------------------------------------------- real lags


def test_a_known_lag_is_measured_exactly_and_its_leader_is_stable():
    feed = repricings()
    rep = compare(stream("A", capture(feed)), stream("B", capture(feed, lambda i: 2.0)))

    n = len(rep.matched)
    assert n == rep.moves_a
    assert rep.lags() == pytest.approx([2.0] * n)
    assert rep.order() == {"a": n, "=": 0, "b": 0}
    st = rep.stability()
    assert st.leader == "a" and st.stable
    assert st.by_match == {"a": 6, "b": 0, "=": 0}


def test_a_lag_near_the_gap_between_moves_keeps_the_order():
    """A moves at 0 s and 5 s, B at 4 s and 9 s: B is 4 s behind, twice.

    Pairing each move with its nearest neighbour would pair A's second move
    with B's first -- B *ahead* by 1 s -- and leave the rest unpaired, which
    turns a lag into simultaneity. Keeping both streams in order does not.
    """
    a = [move(0, 0.50, 0.55), move(5, 0.55, 0.60)]
    b = [move(4, 0.50, 0.55), move(9, 0.55, 0.60)]

    pairs = pair_moves(a, b)

    assert [(ea.ts_received_ns, eb.ts_received_ns) for ea, eb in pairs] == [
        (stamp(0)[0], stamp(4)[0]), (stamp(5)[0], stamp(9)[0])]


def test_moves_in_opposite_directions_do_not_pair():
    assert pair_moves([move(0, 0.50, 0.55)], [move(1, 0.55, 0.50)]) == []


def test_a_move_beyond_the_window_stays_unpaired():
    assert pair_moves([move(0, 0.50, 0.55)], [move(10.5, 0.50, 0.55)]) == []
    assert len(pair_moves([move(0, 0.50, 0.55)], [move(10.5, 0.50, 0.55)],
                          window_s=11)) == 1


def test_a_lag_longer_than_the_window_is_flagged_not_believed():
    """Measured on a real capture delayed by 12 s: under a 10 s window 31% of
    moves paired, none at 12 s, median +0.91 s -- the pairing reached for the
    neighbouring move. Doubling the window exposes that."""
    feed = repricings()
    a, b = stream("A", capture(feed)), stream("B", capture(feed, lambda i: 12.0))

    narrow, wide = compare(a, b, window_s=10), compare(a, b, window_s=20)

    assert not robust_to_window(narrow, wide)
    assert wide.lags() == pytest.approx([12.0] * len(wide.matched))
    assert len(wide.matched) == wide.moves_a


def test_a_lag_inside_the_window_survives_a_wider_one():
    feed = repricings()
    a, b = stream("A", capture(feed)), stream("B", capture(feed, lambda i: 2.0))
    assert robust_to_window(compare(a, b, window_s=10), compare(a, b, window_s=20))


def test_a_lag_across_a_wall_clock_step_is_read_on_the_monotonic_clock():
    """On 23.09 the laptop's wall clock stepped by 6.6 s on waking. Captures on
    one machine share the step, but a pair of moves on either side of it would
    be 6.6 s off on the wall clock; the monotonic one does not step."""
    a = move(20.0, 0.50, 0.55)
    wall, mono = stamp(20.5)
    b = PriceEvent(wall - 6_600_000_000, mono, MID, WINNER, "П1", 0.55, 0.50)

    ((ea, eb),) = pair_moves([a], [b])
    (m,) = compare(Stream("A", "x", "", [a], []), Stream("B", "x", "", [b], [])).matched

    assert m.lag_s == pytest.approx(0.5)


# --------------------------------------------------------------- from quotes to moves


def test_the_price_is_the_books_margin_free_probability():
    quotes = [quote(0, "П1", 1.80), quote(0, "П2", 2.05)]

    assert {e.outcome: e.prob for e in fold(quotes)} == pytest.approx(
        dict(zip(["П1", "П2"], shin([1.80, 2.05])[0])))
    assert {e.outcome: e.prob for e in fold(quotes, method="proportional")} == pytest.approx(
        dict(zip(["П1", "П2"], normalize_proportional([1.80, 2.05]))))


def test_a_book_pushed_outcome_by_outcome_moves_once_at_its_first_frame():
    """Match 5961556, set 2 game 7, 22.09: both outcomes moved in one step,
    2.30/1.47 to 2.35/1.45. Pushed one stake per frame, they arrive apart."""
    quotes = [quote(0, "П1", 2.30), quote(0.001, "П2", 1.47),
              quote(20, "П1", 2.35), quote(20.004, "П2", 1.45)]

    moves = [e for e in fold(quotes) if e.is_move]

    assert [e.outcome for e in moves] == ["П1", "П2"]
    assert all(e.ts_received_ns == stamp(20)[0] for e in moves)
    assert [e.prob for e in moves] == pytest.approx(shin([2.35, 1.45])[0])


def test_without_settling_a_half_repriced_book_is_a_move():
    """Why `SETTLE_S` exists: priced after each frame, the book above moves
    twice, the first time to 2.35 against 1.47 -- a price never quoted."""
    quotes = [quote(0, "П1", 2.30), quote(0.001, "П2", 1.47),
              quote(20, "П1", 2.35), quote(20.004, "П2", 1.45)]

    moves = [e for e in fold(quotes, settle_s=0) if e.is_move]

    assert len(moves) == 4
    assert moves[0].prob == pytest.approx(shin([2.35, 1.47])[0][0])


def test_a_suspended_book_is_not_priced_and_reopening_is_one_move():
    """1.01, 100.0 and 0.0 all sit on closed stakes live: placeholders, not
    prices. A suspension that reopens at a new price is one move, from the
    last real price to the new one, timed at the reopening."""
    quotes = [quote(0, "П1", 2.30), quote(0, "П2", 1.47),
              quote(10, "П1", 100.0, active=False), quote(10, "П2", 1.01, active=False),
              quote(15, "П1", 2.50), quote(15, "П2", 1.40)]

    events = fold(quotes)
    moves = [e for e in events if e.is_move]

    assert len(events) == 4
    assert {e.ts_received_ns for e in moves} == {stamp(15)[0]}
    assert moves[0].prev == pytest.approx(shin([2.30, 1.47])[0][0])


def test_an_outcome_gone_from_the_board_leaves_its_book_unpriced():
    quotes = [quote(0, "П1", 2.30), quote(0, "П2", 1.47),
              quote(5, "П2", None), quote(6, "П1", 2.50)]
    assert not any(e.is_move for e in fold(quotes))


def test_nothing_is_a_move_across_a_break():
    """A move across a reconnect or a silence would be timed when the capture
    came back, not when the book moved."""
    quotes = [quote(0, "П1", 2.30), quote(0, "П2", 1.47), None,
              quote(30, "П1", 2.50), quote(30, "П2", 1.40)]

    events = fold(quotes)

    assert len(events) == 4
    assert not any(e.is_move for e in events)


def test_min_move_lets_small_steps_add_up():
    """One tick on 2.00 against 1.80 is 0.0012 of probability; four add up
    to 0.0049. A 0.002 threshold emits every second step, measured from the
    last emitted value, not from the one before."""
    quotes = []
    for k, o1 in enumerate([2.00, 2.01, 2.02, 2.03, 2.04]):
        quotes += [quote(10 * k, "П1", o1), quote(10 * k, "П2", 1.80)]

    every = [e for e in fold(quotes) if e.is_move and e.outcome == "П1"]
    coarse = [e for e in fold(quotes, min_move=0.002) if e.is_move and e.outcome == "П1"]

    assert len(every) == 4
    assert [e.ts_received_ns for e in coarse] == [stamp(20)[0], stamp(40)[0]]
    assert coarse[0].prev == every[0].prev
    assert coarse[1].prev == coarse[0].prob


def test_a_two_way_book_moving_is_one_move_not_two():
    """Its second outcome is the first mirrored; counted apart, every move
    would count twice and every interval would look twice as sure."""
    feed = repricings(n_matches=1, per_match=5)
    rep = compare(stream("A", capture(feed)), stream("B", capture(feed, lambda i: 1.5)))

    assert rep.outcomes == 2
    assert all(m.outcomes == ("П1", "П2") for m in rep.matched)
    assert len(rep.matched) == rep.moves_a == 4      # five prices, the first is no move


# --------------------------------------------------------------- stability


def move(t, prev, prob, match=MID, outcome="П1"):
    wall, mono = stamp(t)
    return PriceEvent(wall, mono, match, WINNER, outcome, prob, prev)


def matched(leads):
    """MatchedMoves from {match: [lag_s, ...]}."""
    out = []
    for match, lags in leads.items():
        for k, lag in enumerate(lags):
            e = move(k * 20, 0.5, 0.55, match=match)
            out.append(MatchedMove(match, WINNER, ("П1",), e, e, lag))
    return out


def test_a_leader_that_flips_between_matches_is_no_leader():
    st = stability(matched({1: [2.0] * 10, 2: [2.0] * 10, 3: [2.0] * 10,
                            4: [-2.0] * 10, 5: [-2.0] * 10, 6: [-2.0] * 10}))
    assert st.leader is None and not st.stable
    assert st.by_match == {"a": 3, "b": 3, "=": 0}


def test_a_lead_carried_by_one_busy_match_is_not_stable():
    st = stability(matched({1: [2.0] * 60, 2: [-2.0] * 6, 3: [-2.0] * 6, 4: [-2.0] * 6}))
    assert st.leader == "a"
    assert not st.stable
    assert st.verdict.startswith("unstable")


def test_a_lead_over_too_few_matches_is_not_called_stable():
    st = stability(matched({1: [2.0] * 30}))
    assert st.leader == "a" and not st.stable
    assert "too few" in st.verdict


def test_a_lead_that_holds_match_by_match_is_stable():
    st = stability(matched({m: [2.0] * 6 + [-2.0] for m in range(1, 5)}))
    assert st.leader == "a" and st.stable
    assert st.by_match == {"a": 4, "b": 0, "=": 0}


def test_level_moves_decide_nothing():
    st = stability(matched({1: [0.3, -0.5, TIE_S] * 5}))
    assert (st.a_first, st.b_first, st.leader) == (0, 0, None)


def test_wilson_interval():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(14, 27)
    assert lo < 0.5 < hi
    lo, hi = wilson(90, 100)
    assert 0.82 < lo < 0.83 and 0.94 < hi < 0.95


def test_quantile_interpolates_between_ranks():
    assert quantile([3.0], 0.9) == 3.0
    assert quantile([0.0, 10.0], 0.9) == pytest.approx(9.0)
    assert quantile(range(11), 0.5) == 5


# --------------------------------------------------------------- one clock


def run_clock(run_id, start_s, seconds, boot=BOOT, step_at_s=None, step_s=0.0):
    walls, anchors = [], []
    for k in range(int(seconds)):
        t = start_s + k
        stepped = step_at_s is not None and t >= step_at_s
        wall = T0 + int(t * 1e9) + (int(step_s * 1e9) if stepped else 0)
        walls.append(wall)
        anchors.append(boot + (int(step_s * 1e9) if stepped else 0))
    return RunClock(run_id, walls, anchors)


def test_two_captures_on_one_machine_share_a_clock():
    assert clock_offset(run_clock("a", 0, 1800), run_clock("b", 120, 1800)) == 0.0


def test_a_wall_step_seen_by_both_captures_does_not_split_them():
    a = run_clock("a", 0, 1800, step_at_s=900, step_s=-6.6)
    b = run_clock("b", 60, 1800, step_at_s=900, step_s=-6.6)
    assert abs(clock_offset(a, b)) < 1e-3


def test_a_step_back_while_both_run_does_not_split_one_machine():
    """Found in review. After a backward step a wall reading occurs twice, and
    the sample nearest in wall time can sit on the wrong side of the step: two
    recorders on one machine stepping back 60 s, the second started 16 s later,
    came out 60 s apart and were refused as two machines."""
    a = run_clock("a", 34, 103, step_at_s=89, step_s=-60)
    b = run_clock("b", 50, 103, step_at_s=89, step_s=-60)

    assert clock_offset(a, b) == 0.0
    streams = [Stream("A", "betboom", "", [], [a]), Stream("B", "1win", "", [], [b])]
    assert check_same_clock(streams) == {("A", "B"): 0.0}


def test_two_machines_are_refused_across_a_step_too():
    a = run_clock("a", 0, 600, step_at_s=300, step_s=-6.6)
    b = run_clock("b", 0, 600, boot=BOOT + 3_200_000_000, step_at_s=300, step_s=-6.6)
    assert clock_offset(a, b) == pytest.approx(-3.2)


def test_captures_from_two_machines_are_refused():
    """Two machines differ by their boot times; seconds are the least of it."""
    a = Stream("A", "betboom", "", [], [run_clock("a", 0, 600)])
    b = Stream("B", "1win", "", [], [run_clock("b", 0, 600, boot=BOOT + 3_200_000_000)])
    with pytest.raises(ClockMismatch, match="one machine"):
        check_same_clock([a, b])


def test_captures_that_never_ran_together_are_not_compared():
    a = Stream("A", "betboom", "", [], [run_clock("a", 0, 600)])
    b = Stream("B", "betboom", "", [], [run_clock("b", 7200, 600, boot=BOOT + 3_200_000_000)])
    assert check_same_clock([a, b]) == {("A", "B"): None}


# --------------------------------------------------------------- logs on disk


def record(root, frames, *, boot=BOOT, provider="betboom", breaks=()):
    """Write (arrival, match, outcome, odds) frames as one recorder whose
    machine has wall - mono = `boot`; a reconnect is logged before each
    arrival time in `breaks`."""
    clock = FakeClock(wall_ns=T0, mono_ns=T0 - boot)
    pending = sorted(breaks)
    with RawLog(root, provider=provider, clock=clock, compress=False) as log:
        now = 0.0
        for t, match, outcome, odds in frames:
            clock.advance(t - now)
            now = t
            while pending and pending[0] <= t:
                pending.pop(0)
                log.write("reconnect after test", direction="meta", channel="_conn")
            log.write(json.dumps({"match": match, "market": list(WINNER),
                                  "outcome": outcome, "odds": odds}, ensure_ascii=False),
                      channel="test")
    return root


def json_quotes(rows):
    """The test decoder: frames written by `record` back to quotes."""
    for row in rows:
        if row is None:
            yield None
        elif row.get("dir") == "rx":
            d = json.loads(row["payload"])
            yield Quote(row["ts_received_ns"], row["ts_mono_ns"], d["match"],
                        tuple(d["market"]), d["outcome"], d["odds"])


def test_two_machines_are_refused_end_to_end(tmp_path):
    feed = repricings(n_matches=2, per_match=10)
    record(tmp_path / "a", heard(feed))
    record(tmp_path / "b", heard(feed), boot=BOOT + 3_600_000_000_000)
    a = load_stream("A", tmp_path / "a", decoder=json_quotes)
    b = load_stream("B", tmp_path / "b", decoder=json_quotes)
    with pytest.raises(ClockMismatch):
        check_same_clock([a, b])


def test_one_stream_holding_two_simultaneous_captures_is_refused(tmp_path):
    """Two recorders at once are two streams -- the zero test -- and folded
    together they would interleave into moves neither of them saw."""
    feed = repricings(n_matches=2, per_match=10)
    record(tmp_path / "both", heard(feed))
    record(tmp_path / "both", heard(feed, lambda i: 0.02))
    with pytest.raises(ValueError, match="same time"):
        load_stream("A", tmp_path / "both", decoder=json_quotes)


def test_a_reconnect_and_a_silence_both_break_the_capture(tmp_path):
    frames = [(0.0, MID, "П1", 2.30), (0.0, MID, "П2", 1.47),
              (20.0, MID, "П1", 2.40), (20.0, MID, "П2", 1.42),     # after a reconnect
              (100.0, MID, "П1", 2.50), (100.0, MID, "П2", 1.40)]   # after 80 s of silence
    record(tmp_path / "a", frames, breaks=[10.0])

    events = load_stream("A", tmp_path / "a", decoder=json_quotes).events

    assert len(events) == 6
    assert not any(e.is_move for e in events)


def test_a_1win_log_is_refused_until_its_odds_are_known(tmp_path):
    """The 1win recorder logs the known endpoints, none of which carries a
    price. Folding those into an empty stream would read as "1win never
    moves"; the decoder is refused by name instead."""
    with RawLog(tmp_path, provider="1win", clock=FakeClock(), compress=False) as log:
        log.write(b'{"id": 31415, "service": "LIVE"}', channel="matches/get")
    with pytest.raises(NotImplementedError, match="1win"):
        load_stream("1win", tmp_path)


def test_a_hive_style_path_is_a_path_not_a_name(tmp_path):
    hive = tmp_path / "provider=betboom"
    hive.mkdir()
    assert _stream_arg(str(hive), 0) == ("A", hive)
    assert _stream_arg(f"bb={hive}", 1) == ("bb", hive)


# --------------------------------------------------------------- BetBoom frames


@pytest.fixture(scope="module")
def pb():
    sys.path.insert(0, str(GENERATED))
    return pytest.importorskip(
        "bb_sport_ws_v1_pb2",
        reason="generated protobuf classes missing; build them with grpc_tools.protoc "
               "(see tennis/ingest/betboom/README.md)")


GAME_7 = "2-й сет 7-й гейм: Исход"
TOTAL = "1-й сет: Тотал"


def add(match, market, name, factor, *, active=True, stake_id="", argument=None):
    s = match.stakes.add()
    s.match_id, s.market_name, s.name = MID, market, name
    s.factor, s.is_active, s.stake_id = factor, active, stake_id
    if argument is not None:
        s.argument = argument
    return s


def row(pb, t, fill):
    msg = pb.MainResponse()
    fill(msg)
    wall, mono = stamp(t)
    return {"dir": "rx", "payload": msg.SerializeToString(),
            "ts_received_ns": wall, "ts_mono_ns": mono}


def full(pb, t, stakes, action=None):
    def fill(msg):
        body = msg.newsletters_full_match
        body.action = action if action is not None else pb.NEWSLETTER_ACTIONS_UPDATE
        body.match.info.id = MID
        for s in stakes:
            add(body.match, *s[:3], **s[3] if len(s) > 3 else {})
    return row(pb, t, fill)


def tour(pb, t, stakes):
    def fill(msg):
        body = msg.newsletters_match
        body.action = pb.NEWSLETTER_ACTIONS_UPDATE
        body.match.info.id = MID
        for s in stakes:
            add(body.match, *s)
    return row(pb, t, fill)


def push(pb, t, name, factor, *, market=GAME_7, stake_id="", action=None, match_id=MID):
    def fill(msg):
        body = msg.newsletters_stake
        body.action = action if action is not None else pb.NEWSLETTER_ACTIONS_UPDATE
        s = body.stake
        s.match_id, s.market_name, s.name = match_id, market, name
        s.factor, s.is_active, s.stake_id = factor, True, stake_id
    return row(pb, t, fill)


def seen(quotes):
    return [(q.market[3], q.outcome, q.odds, q.active) for q in quotes if q is not None]


def test_full_snapshots_yield_only_what_changed_and_no_lines(pb):
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47),
                         (TOTAL, "Больше (9.5)", 1.85, {"argument": 9.5}),
                         (TOTAL, "Меньше (9.5)", 1.95, {"argument": 9.5})]),
            full(pb, 12, [(GAME_7, "П1", 2.35), (GAME_7, "П2", 1.47)])]

    assert seen(betboom_quotes(rows, pb)) == [
        ("Исход", "П1", 2.30, True), ("Исход", "П2", 1.47, True),
        ("Исход", "П1", 2.35, True)]


def test_a_full_snapshot_without_stakes_is_not_an_empty_board(pb):
    """UPDATE_INFO and DELETE came with no stakes in 46 of 2351 live snapshots."""
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)]),
            full(pb, 5, [], action=pb.NEWSLETTER_ACTIONS_UPDATE_INFO),
            full(pb, 12, [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)])]
    assert len(seen(betboom_quotes(rows, pb))) == 2


def test_an_outcome_missing_from_a_full_snapshot_is_gone(pb):
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)]),
            full(pb, 12, [(GAME_7, "П1", 2.30)])]
    assert seen(betboom_quotes(rows, pb))[-1] == ("Исход", "П2", None, False)


def test_an_outcome_missing_from_a_tour_frame_is_not_gone(pb):
    """A tour frame carries headline odds only; 355 of 6940 live carried none."""
    rows = [tour(pb, 0, [("Исход", "П1", 1.80), ("Исход", "П2", 2.05)]),
            tour(pb, 3, [("Исход", "П1", 1.85)])]
    assert seen(betboom_quotes(rows, pb)) == [
        ("Исход", "П1", 1.80, True), ("Исход", "П2", 2.05, True), ("Исход", "П1", 1.85, True)]


def test_a_source_repeating_itself_does_not_undo_a_move(pb):
    """The snapshot moved the match winner; the tour frame after it still says
    what it said before. Taken as news, it would move the price back, and a
    lagging source would invent a move in the other direction."""
    rows = [full(pb, 0, [("Исход", "П1", 1.80), ("Исход", "П2", 2.05)]),
            tour(pb, 0.01, [("Исход", "П1", 1.80), ("Исход", "П2", 2.05)]),
            full(pb, 10, [("Исход", "П1", 1.90), ("Исход", "П2", 1.95)]),
            tour(pb, 11, [("Исход", "П1", 1.80), ("Исход", "П2", 2.05)])]

    moves = [e for e in fold(betboom_quotes(rows, pb)) if e.is_move]

    assert {e.ts_received_ns for e in moves} == {stamp(10)[0]}


def test_a_stake_push_is_keyed_like_the_snapshot_that_named_it(pb):
    """Pushes name the stake by id; the recorder takes the ids from the
    snapshot, so the snapshot's market and outcome are the key either way."""
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30, {"stake_id": "7145465172"}),
                         (GAME_7, "П2", 1.47, {"stake_id": "7145465173"})]),
            push(pb, 3, "", 2.35, market="", stake_id="7145465172"),
            push(pb, 3.004, "П2", 1.45, stake_id="7145465173"),
            push(pb, 4, "", 9.99, market="", stake_id="unknown")]

    assert seen(betboom_quotes(rows, pb))[2:] == [
        ("Исход", "П1", 2.35, True), ("Исход", "П2", 1.45, True)]


def test_a_stake_id_is_known_only_within_its_match(pb):
    """A capture runs through many matches; an id named in one must not name
    a stake of another, should the feed ever reuse it."""
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30, {"stake_id": "7145465172"}),
                         (GAME_7, "П2", 1.47, {"stake_id": "7145465173"})]),
            push(pb, 3, "", 2.35, market="", stake_id="7145465172", match_id=MID + 1)]

    assert len(seen(betboom_quotes(rows, pb))) == 2


def test_a_deleted_stake_is_gone(pb):
    rows = [push(pb, 0, "П1", 2.30, action=pb.NEWSLETTER_ACTIONS_DELETE)]
    assert seen(betboom_quotes(rows, pb)) == [("Исход", "П1", None, False)]


def test_sources_pick_the_frame_kinds(pb):
    rows = [full(pb, 0, [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)]),
            tour(pb, 1, [("Исход", "П1", 1.80), ("Исход", "П2", 2.05)]),
            push(pb, 2, "П1", 2.35)]
    assert [q.market[0] for q in betboom_quotes(rows, pb, ("tour",))] == ["match", "match"]
    assert [q.ts_received_ns for q in betboom_quotes(rows, pb, ("stake",))] == [stamp(2)[0]]


def test_betboom_zero_test_through_the_command_line(pb, tmp_path, capsys):
    """Two recorders, one machine, one feed, real protobuf frames, the CLI."""
    feed = repricings(n_matches=4, per_match=20)
    for name, seed in (("a", 1), ("b", 2)):
        clock = FakeClock(wall_ns=T0, mono_ns=T0 - BOOT)
        with RawLog(tmp_path / name, provider="betboom", clock=clock) as log:
            now = 0.0
            for t, match, outcome, odds in heard(feed, jitter(seed)):
                clock.advance(t - now)
                now = t
                msg = pb.MainResponse()
                body = msg.newsletters_match
                body.match.info.id = match
                s = body.match.stakes.add()
                s.match_id, s.market_name, s.name, s.factor, s.is_active = (
                    match, "Исход", outcome, odds, True)
                log.write(msg.SerializeToString(), channel="tree_ws")

    assert main([f"A={tmp_path / 'a'}", f"B={tmp_path / 'b'}"]) == 0
    out = capsys.readouterr().out
    assert "same clock: offsets agree to 0.0 ms" in out
    assert "no leader" in out
    assert "WARNING" not in out


def test_the_command_line_refuses_two_machines(pb, tmp_path, capsys):
    """An answer, not a crash: exit 2 and the reason, before any pairing."""
    for name, boot in (("a", BOOT), ("b", BOOT + 3_600_000_000_000)):
        clock = FakeClock(wall_ns=T0, mono_ns=T0 - boot)
        with RawLog(tmp_path / name, provider="betboom", clock=clock) as log:
            for _ in range(30):
                clock.advance(1.0)
                log.write(pb.MainResponse().SerializeToString(), channel="tree_ws")

    assert main([f"A={tmp_path / 'a'}", f"B={tmp_path / 'b'}"]) == 2
    assert "not written on one machine" in capsys.readouterr().err
