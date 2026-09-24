"""Tests for p_gap: how far two books disagree on the server's point probability.

The books below are shaped after the first two-book trial, 23.09 on the
owner's laptop: two-way "winner of the game" markets at 5-10% margin, 1win
following BetBoom by about two seconds, and BetBoom pricing the next game
while the one before it is played.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog
from tennis.market.measure import AWAY, HOME, GameContext
from tennis.market.overround import shin
from tennis.market.p_gap import (
    GameGap,
    game_gaps,
    games_needed,
    last_settled,
    main,
    mean_ci,
    median_ci,
    p_from_hold,
    print_report,
    timelines,
)
from tennis.market.streams import Quote
from tennis.markov import p_game

S = 1_000_000_000                       # a second in ns
GAME5 = ("game", 1, 5, "Исход")


def q(t, match, outcome, odds, active=True, market=GAME5, wall_back=0.0):
    """A quote at `t` s of the monotonic clock; the wall clock `wall_back` s behind."""
    return Quote(int((t - wall_back) * S), int(t * S), match, market, outcome, odds, active)


def book(t, match, home, away, market=GAME5):
    """A two-way book pushed outcome by outcome, a millisecond apart."""
    return [q(t, match, "П1", home, market=market), q(t + 0.001, match, "П2", away, market=market)]


def gap(match, dp, p=0.6):
    return GameGap(match, "WTT", 1, 5, 0, p_game(p), p_game(p + dp), p, p + dp, 0.07, 0.10)


# --------------------------------------------------------------- p from P(hold)


@pytest.mark.parametrize("p", [i / 20 for i in range(1, 20)])
def test_p_comes_back_from_its_hold_on_both_sides_of_a_half(p):
    """The old invert() returned the baseline for every probability under 0.5:
    its search looked on one side only. This one must not."""
    assert p_from_hold(p_game(p)) == pytest.approx(p, abs=1e-9)


@pytest.mark.parametrize("hold", [0.2, 0.55, 0.69, 0.9])
def test_the_gap_does_not_depend_on_who_serves(hold):
    """A game is won by the server with p as often as it is lost by one with
    1 - p, so mistaking the server flips both books' p and keeps the gap."""
    assert p_from_hold(1 - hold) == pytest.approx(1 - p_from_hold(hold), abs=1e-9)


@pytest.mark.parametrize("hold", [0.0, 1.0])
def test_a_certain_hold_has_no_p(hold):
    with pytest.raises(ValueError):
        p_from_hold(hold)


# --------------------------------------------------------------- books over time


def test_a_book_is_open_only_while_both_outcomes_can_be_bet():
    quotes = [*book(1, 7, 2.4, 1.55),
              q(20, 7, "П1", 2.4, active=False),       # suspended
              *book(21, 7, 2.6, 1.48),
              q(30, 7, "П2", None)]                     # off the board
    line = timelines(quotes)[(7, GAME5)]
    states = [s for _, s in line]
    # The repricing at 21 s passes a millisecond through half-new odds; a
    # moment must hold still for seconds, so no such state is ever compared.
    assert states == [None, (("П1", 2.4), ("П2", 1.55)), None,
                      (("П1", 2.6), ("П2", 1.55)), (("П1", 2.6), ("П2", 1.48)), None]
    assert line[1][0] == quotes[1].ts_mono_ns          # open once the second outcome came


def test_a_break_shuts_every_book_at_the_last_time_seen():
    quotes = [*book(1, 7, 2.4, 1.55), *book(3, 8, 1.9, 1.9), None, *book(90, 7, 2.5, 1.5)]
    last = quotes[3].ts_mono_ns
    lines = timelines(quotes)
    assert lines[(7, GAME5)][2] == (last, None)
    assert lines[(8, GAME5)][-1] == (last, None)
    assert lines[(7, GAME5)][-1][1] == (("П1", 2.5), ("П2", 1.5))


def test_renaming_puts_the_other_book_in_the_first_one_s_names():
    quotes = [q(1, 40, "1", 1.6), q(1.001, 40, "2", 2.3), q(1, 41, "1", 1.9)]
    sides = {40: (7, {"1": "П2", "2": "П1"})}          # home and away the other way round

    def rename(quote):
        if quote.match not in sides:
            return None
        match, names = sides[quote.match]
        return match, names[quote.outcome]

    lines = timelines(quotes, rename)
    assert list(lines) == [(7, GAME5)]
    assert lines[(7, GAME5)][-1][1] == (("П1", 2.3), ("П2", 1.6))


# --------------------------------------------------------------- the moment


A = (("П1", 2.4), ("П2", 1.55))
B = (("П1", 2.3), ("П2", 1.6))
B2 = (("П1", 2.2), ("П2", 1.65))
ALWAYS = lambda t: True                                  # noqa: E731


def test_the_moment_is_the_last_before_the_market_shuts():
    a = [(0, A), (30 * S, None)]
    b = [(2 * S, B), (31 * S, None)]
    assert last_settled(a, b, ALWAYS, 10 * S) == (30 * S, A, B)


def test_of_two_settled_moments_the_later_one_is_taken():
    """Found in review: taking the first settled moment instead of the last
    moved the trial's gap from 1.53 to 1.37 points, and no test noticed."""
    A2 = (("П1", 2.5), ("П2", 1.5))
    a = [(0, A), (20 * S, A2), (40 * S, None)]
    b = [(2 * S, B), (41 * S, None)]
    assert last_settled(a, b, ALWAYS, 10 * S) == (40 * S, A2, B)


def test_a_book_that_just_moved_is_not_settled():
    """1win follows BetBoom by about 2 s: compared right after a move, the
    two books differ by the lag, not by their opinions."""
    a = [(0, A), (30 * S, None)]
    b = [(2 * S, B), (25 * S, B2), (31 * S, None)]
    assert last_settled(a, b, ALWAYS, 10 * S) == (25 * S, A, B)


def test_no_moment_while_a_book_is_shut_or_the_game_has_begun():
    a = [(0, A), (5 * S, None), (40 * S, A), (45 * S, None)]
    b = [(2 * S, B), (60 * S, None)]
    assert last_settled(a, b, ALWAYS, 10 * S) is None
    assert last_settled([(0, A), (30 * S, None)], [(2 * S, B), (31 * S, None)],
                        lambda t: False, 10 * S) is None


# --------------------------------------------------------------- games


def two_books(bb=(2.4, 1.55), ow=(2.3, 1.6), market=GAME5):
    a = timelines([*book(1, 7, *bb, market=market), q(40, 7, "П1", None, market=market)])
    b = timelines([*book(3, 7, *ow, market=market), q(41, 7, "П1", None, market=market)])
    return a, b


def test_the_next_game_is_read_for_its_own_server():
    """BetBoom prices the next game; its server is the other player. Reading
    the scoreboard's server would mirror the hold (measure.GameContext)."""
    a, b = two_books()
    boards = {7: [(0, GameContext(set_no=1, game_no=4, server=HOME))]}

    (g,) = game_gaps(a, b, boards, {7: "WTT"})

    assert g.hold_a == pytest.approx(shin([2.4, 1.55])[0][1])      # П2 serves game 5
    assert g.hold_b == pytest.approx(shin([2.3, 1.6])[0][1])
    assert g.p_a == pytest.approx(p_from_hold(g.hold_a))
    assert g.hold_a > 0.5 and g.dp < 0          # 1win has П2 at 1.6, BetBoom at 1.55
    assert (g.tier, g.set_no, g.game_no, g.ts_mono_ns) == ("WTT", 1, 5, 40 * S)
    assert g.margin_a == pytest.approx(1 / 2.4 + 1 / 1.55 - 1)


@pytest.mark.parametrize("ctx", [
    GameContext(set_no=1, game_no=5, server=AWAY),      # game 5 itself is being played
    GameContext(set_no=1, game_no=3, server=AWAY),      # two games ahead
    GameContext(set_no=2, game_no=4, server=AWAY),      # another set
])
def test_only_a_game_not_begun_while_the_one_before_it_is_played(ctx):
    a, b = two_books()
    assert game_gaps(a, b, {7: [(0, ctx)]}, {}) == []


def test_a_tiebreak_is_left_out():
    """At 6-6 the serve goes 1-2-2, and server_of does not guess."""
    game13 = ("game", 1, 13, "Исход")
    a, b = two_books(market=game13)
    assert game_gaps(a, b, {7: [(0, GameContext(set_no=1, game_no=12, server=HOME))]}, {}) == []


def test_the_scoreboard_is_read_as_it_stood_at_the_moment():
    """Game 5 began at 35 s: the moment before the market shut (40 s) is out,
    and BetBoom's repricing at 30 s -- still during game 4 -- is the last in."""
    a = timelines([*book(1, 7, 2.4, 1.55), *book(30, 7, 2.5, 1.5), q(40, 7, "П1", None)])
    _, b = two_books()
    boards = {7: [(0, GameContext(set_no=1, game_no=4, server=HOME)),
                  (35 * S, GameContext(set_no=1, game_no=5, server=AWAY))]}
    (g,) = game_gaps(a, b, boards, {})
    assert g.ts_mono_ns == 30 * S
    assert g.hold_a == pytest.approx(shin([2.4, 1.55])[0][1])


def test_a_wall_clock_stepping_back_does_not_reorder_a_book():
    """Found in review: ordered by the wall clock, a step back of 20 s put a
    book's changes out of order, and the moment at 140 s compared 1.50/2.50
    with 1.52/2.45 while the boards stood at 1.80/2.00 and 1.82/1.98. The
    monotonic clock does not step."""
    back = 20.0                                          # from 97 s on, the wall is 20 s behind
    a = timelines([*book(10, 7, 2.50, 1.50), *book(95, 7, 2.30, 1.60),
                   q(100, 7, "П1", 2.00, wall_back=back), q(100.001, 7, "П2", 1.80, wall_back=back),
                   q(140, 7, "П1", None, wall_back=back)])
    b = timelines([*book(12, 7, 2.45, 1.52), *book(96, 7, 2.25, 1.62),
                   q(102, 7, "П1", 1.98, wall_back=back), q(102.001, 7, "П2", 1.82, wall_back=back),
                   q(141, 7, "П1", None, wall_back=back)])
    boards = {7: [(0, GameContext(set_no=1, game_no=4, server=HOME))]}

    (g,) = game_gaps(a, b, boards, {})

    assert g.ts_mono_ns == 140 * S
    assert g.hold_a == pytest.approx(shin([2.00, 1.80])[0][1])
    assert g.hold_b == pytest.approx(shin([1.98, 1.82])[0][1])


# --------------------------------------------------------------- the interval


def test_the_interval_resamples_matches_not_games():
    """Twenty games of one match are not twenty witnesses. With one match at
    +2 points and another of twenty games at 0, resampling games would all but
    never see the +2; resampling matches does, a quarter of the time."""
    games = [gap("x", 0.02)] + [gap("y", 0.0) for _ in range(20)]
    mid, lo, hi = median_ci(games, lambda g: abs(g.dp))
    assert mid == pytest.approx(0.0, abs=1e-12)
    assert hi == pytest.approx(0.02)
    assert median_ci(games, lambda g: abs(g.dp)) == (mid, lo, hi)     # seeded


def test_the_interval_is_95_percent_wide():
    """Found in review: 5 and 95 per cent in place of 2.5 and 97.5 passed every
    test. Four hundred matches, half at 1 and half at 0: the mean of a
    resample is near normal, 0.5 with a spread of 0.5 / 20 = 0.025, so its
    95% interval is 0.5 -+ 1.96 x 0.025 = 0.451 to 0.549 (a 90% one would be
    0.459 to 0.541)."""
    games = [gap(f"m{i}", 0.0) for i in range(400)]
    values = {g.match: float(i % 2) for i, g in enumerate(games)}
    mid, lo, hi = mean_ci(games, lambda g: values[g.match])
    assert mid == 0.5
    assert lo == pytest.approx(0.5 - 1.96 * 0.025, abs=0.004)
    assert hi == pytest.approx(0.5 + 1.96 * 0.025, abs=0.004)


def test_games_needed_grow_with_the_square_of_the_precision():
    """Half the width takes four times the games; 1.5 times, 2.25 times."""
    assert games_needed(10, 0.0045, 0.003) == 23             # 10 x 1.5^2 = 22.5
    assert games_needed(10, 0.006, 0.003) == 40              # 10 x 2^2


def test_the_report_says_when_there_is_too_little(capsys):
    games = [gap("x", 0.01), gap("x", 0.03), gap("y", 0.05)]
    print_report(games, "trial", 0.0, 10.0)
    out = capsys.readouterr().out
    assert "3 in 2 matches" in out
    assert "too few matches" in out and "would take about" in out


def test_the_command_refuses_two_captures_of_one_book(tmp_path, capsys):
    from tennis.ingest.rawlog import RawLog
    for name in ("one", "two"):
        with RawLog(tmp_path / name, provider="1win", compress=False) as log:
            log.write("3", channel="push")
    assert main([f"a={tmp_path / 'one'}", f"b={tmp_path / 'two'}"]) == 2
    assert "one BetBoom capture and one 1win" in capsys.readouterr().err


# --------------------------------------------------------------- the command, end to end
#
# Real log files, as the two recorders write them: BetBoom's protobuf frames
# with the scoreboard, the players and a game market; 1win's live list and
# push frames. 1win names the players the other way round from BetBoom, as
# it does live, so its "1" is BetBoom's "П2".

GENERATED = Path(__file__).resolve().parents[2] / "ingest" / "betboom" / "generated"
T0 = 1_790_192_455_000_000_000               # 23.09, the first two-book trial
BOOT = 1_789_742_505_831_000_000             # wall - mono of the machine
BB, OW = 5_967_431, 40_403_794
GAME5_NAME = "1-й сет 5-й гейм: Исход"


@pytest.fixture(scope="module")
def pb():
    sys.path.insert(0, str(GENERATED))
    return pytest.importorskip(
        "bb_sport_ws_v1_pb2",
        reason="generated protobuf classes missing; build them with grpc_tools.protoc "
               "(see tennis/ingest/betboom/README.md)")


def bb_board(pb, game_in_play, game5=None):
    """A full snapshot: the scoreboard at `game_in_play` of set 1, home
    serving it, the match winner, and game 5's winner when `game5` is given."""
    msg = pb.MainResponse()
    body = msg.newsletters_full_match
    body.action = pb.NEWSLETTER_ACTIONS_UPDATE
    m = body.match
    m.info.id = BB
    m.info.teams.home_team.name = "Риналдо Перссон К."
    m.info.teams.away_team.name = "Тимофеева М."
    sb = m.info.scoreboard
    sb.current_game_part, sb.serving_side = 1, HOME
    part = sb.scores.add()
    part.type, part.sequence = pb.SCOREBOARD_SCORE_TYPES_PART, 1
    part.home_score, part.away_score = game_in_play - 1, 0
    stakes = [("Исход", "П1", 1.9), ("Исход", "П2", 1.9)]
    if game5 is not None:
        stakes += [(GAME5_NAME, "П1", game5[0]), (GAME5_NAME, "П2", game5[1])]
    for market, name, factor in stakes:
        s = m.stakes.add()
        s.match_id, s.market_name, s.name, s.factor, s.is_active = BB, market, name, factor, True
        s.stake_id = f"{market}|{name}"
    return msg.SerializeToString()


def ow_live():
    return json.dumps({"result": {"items": [{"id": OW, "sportId": 33, "competitors": [
        {"position": 1, "name": "Мария Тимофеева"},
        {"position": 2, "name": "Кайса Риналдо Перссон"}]}]}}, ensure_ascii=False)


def ow_odds(kind, items):
    group = {"id": 1, "oddsList": items}
    if kind == "match-odds-snapshot":
        group["name"] = "Победитель гейма"
    return "42" + json.dumps(["u", {"data": {"matchId": OW, "oddsGroups": [group]},
                                    "messageType": kind}, "Q"], ensure_ascii=False)


def ow_game5(one, two):
    """Game 5 of set 1 with 1win's "1" (Тимофеева) at `one`, "2" at `two`."""
    return ow_odds("match-odds-snapshot", [
        {"id": 11, "cf": one, "status": 1, "outcome": "1", "vars": {"v1": 1, "v2": 5}},
        {"id": 12, "cf": two, "status": 1, "outcome": "2", "vars": {"v1": 1, "v2": 5}}])


OW_SUSPENDED = ow_odds("match-odds", [{"id": 11, "status": 2}, {"id": 12, "status": 2}])


def run(root, provider, frames, *, start=0.0, boot=BOOT):
    """One recorder run: (t, channel, payload) frames, t seconds after T0, on
    a machine whose wall - mono is `boot`."""
    clock = FakeClock(wall_ns=T0 + int(start * S), mono_ns=T0 + int(start * S) - boot)
    with RawLog(root, provider=provider, clock=clock, compress=False) as log:
        now = start
        for t, channel, payload in frames:
            clock.advance(t - now)
            now = t
            log.write(payload, channel=channel)
    return root


def onewin_run(root, until, odds, *, boot=BOOT):
    """1win's live list, pings every 20 s -- a silence of 60 s is a break --
    and `odds` as (t, payload)."""
    frames = [(0.0, "matches/get-many", ow_live())]
    frames += [(float(t), "push", "2") for t in range(20, int(until), 20)]
    frames += [(t, "push", payload) for t, payload in odds]
    return run(root, "1win", sorted(frames, key=lambda f: f[0]), boot=boot)


def games_of(tmp_path, bb_root, ow_root, capsys):
    out = tmp_path / "games.csv"
    code = main([f"betboom={bb_root}", f"1win={ow_root}", "--games", str(out)])
    err = capsys.readouterr().err
    rows = list(csv.DictReader(out.open(encoding="utf-8"))) if out.exists() else None
    return code, rows, err


def test_one_machine_end_to_end_the_game_is_found_with_1win_s_sides_turned(pb, tmp_path, capsys):
    """Found in review: dropping the clock check or 1win's renaming in `main`
    left every test green. Here the renaming decides whether the game is found
    at all -- 1win's "1" is Тимофеева, BetBoom's away player."""
    bb = run(tmp_path / "bb", "betboom", [(1.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55))),
                                          (20.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55))),
                                          (40.0, "tree_ws", bb_board(pb, 5))])
    ow = onewin_run(tmp_path / "1w", 50, [(3.0, ow_game5(1.6, 2.3)), (41.0, OW_SUSPENDED)])

    code, rows, _ = games_of(tmp_path, bb, ow, capsys)

    assert code == 0
    (g,) = rows
    assert (int(g["match"]), g["set"], g["game"]) == (BB, "1", "5")
    # Game 5 is served by the away player, Тимофеева: П2 at both books.
    assert float(g["hold_betboom"]) == pytest.approx(shin([2.4, 1.55])[0][1], abs=1e-6)
    assert float(g["hold_1win"]) == pytest.approx(shin([2.3, 1.6])[0][1], abs=1e-6)


def test_two_machines_are_refused_end_to_end(pb, tmp_path, capsys):
    bb = run(tmp_path / "bb", "betboom", [(1.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55))),
                                          (40.0, "tree_ws", bb_board(pb, 5))])
    ow = onewin_run(tmp_path / "1w", 50, [(3.0, ow_game5(1.6, 2.3)), (41.0, OW_SUSPENDED)],
                    boot=BOOT + 3_600 * S)

    code, rows, err = games_of(tmp_path, bb, ow, capsys)

    assert code == 2 and rows is None
    assert "refused" in err and "one machine" in err


def test_a_restarted_recorder_s_old_price_is_not_settled(pb, tmp_path, capsys):
    """Found in review: without the break between runs, BetBoom's price from
    before a restart counted as five minutes still, beside a fresh 1win one.
    The first run ends at 100 s at 2.4/1.55; the second starts at 400 s, and
    game 5 begins at 405 s."""
    bb = tmp_path / "bb"
    board4 = bb_board(pb, 4, (2.4, 1.55))
    run(bb, "betboom", [(1.0, "tree_ws", board4)]
        + [(t, "tree_ws", board4) for t in (30.0, 60.0, 90.0, 100.0)])
    run(bb, "betboom", [(400.0, "tree_ws", bb_board(pb, 4, (2.5, 1.5))),
                        (405.0, "tree_ws", bb_board(pb, 5))], start=399.0)
    ow = onewin_run(tmp_path / "1w", 430, [(300.0, ow_game5(1.6, 2.3)), (420.0, OW_SUSPENDED)])

    code, rows, _ = games_of(tmp_path, bb, ow, capsys)

    assert code == 0 and rows == []


def test_a_reboot_between_runs_is_refused(pb, tmp_path, capsys):
    """A reboot restarts the monotonic clock the moments are ordered on."""
    bb = tmp_path / "bb"
    run(bb, "betboom", [(1.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55))),
                        (50.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55)))])
    run(bb, "betboom", [(400.0, "tree_ws", bb_board(pb, 4, (2.4, 1.55)))],
        start=399.0, boot=BOOT + 10_000 * S)
    ow = onewin_run(tmp_path / "1w", 50, [(3.0, ow_game5(1.6, 2.3))])

    code, rows, err = games_of(tmp_path, bb, ow, capsys)

    assert code == 2 and rows is None
    assert "reboot" in err
