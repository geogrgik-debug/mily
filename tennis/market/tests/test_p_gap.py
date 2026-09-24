"""Tests for p_gap: how far two books disagree on the server's point probability.

The books below are shaped after the first two-book trial, 23.09 on the
owner's laptop: two-way "winner of the game" markets at 5-10% margin, 1win
following BetBoom by about two seconds, and BetBoom pricing the next game
while the one before it is played.
"""
import pytest

from tennis.market.measure import AWAY, HOME, GameContext
from tennis.market.overround import shin
from tennis.market.p_gap import (
    GameGap,
    game_gaps,
    last_settled,
    main,
    median_ci,
    p_from_hold,
    print_report,
    timelines,
)
from tennis.market.streams import Quote
from tennis.markov import p_game

S = 1_000_000_000                       # a second in ns
GAME5 = ("game", 1, 5, "Исход")


def q(t, match, outcome, odds, active=True, market=GAME5):
    return Quote(int(t * S), int(t * S), match, market, outcome, odds, active)


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
    assert line[1][0] == quotes[1].ts_received_ns       # open once the second outcome came


def test_a_break_shuts_every_book_at_the_last_time_seen():
    quotes = [*book(1, 7, 2.4, 1.55), *book(3, 8, 1.9, 1.9), None, *book(90, 7, 2.5, 1.5)]
    last = quotes[3].ts_received_ns
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
    assert (g.tier, g.set_no, g.game_no, g.ts_received_ns) == ("WTT", 1, 5, 40 * S)
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
    assert g.ts_received_ns == 30 * S
    assert g.hold_a == pytest.approx(shin([2.4, 1.55])[0][1])


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
