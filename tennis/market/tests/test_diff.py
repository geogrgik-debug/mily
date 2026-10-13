"""Tests for the difference between two consecutive board snapshots.

The shapes and numbers here were measured on the capture of 2026-09-22, not
invented: 1,597 full snapshots of 20 matches carrying 176,995 quotes, 1,577
consecutive pairs of the same match. Where a test pins a price, the price was
quoted; the stake ids are the feed's own, verbatim.
"""
from dataclasses import dataclass

import pytest

from tennis.market import MarketRef
from tennis.market.diff import PriceChange, diff_snapshots


@dataclass(frozen=True)
class Stake:
    """The fields of `models_ModelsStake` that a diff reads.

    A protobuf stake satisfies this shape already; the diff duck-types rather
    than converting, so nothing here has to be kept in step with the recovered
    schema beyond these names.
    """

    market_name: str
    name: str
    factor: float
    is_active: bool = True
    match_id: int = 0


def game_winner(outcome, factor, is_active=True, game=7, set_=2, match_id=0):
    return Stake(market_name=f"{set_}-й сет {game}-й гейм: Исход",
                 name=outcome, factor=factor, is_active=is_active,
                 match_id=match_id)


# --------------------------------------------------------------- real data


def test_a_real_price_move_on_the_game_winner_market():
    """Match 5961556, set 2 game 7, two consecutive snapshots, 2026-09-22.

    Both outcomes moved in one step and both stayed open. Their stake ids on
    the feed were `sr:match:74929516/210/4?gamenr=7&setnr=2` and `.../5?...`:
    the address of the game is carried inside the id, which is why the pair
    of quotes is the same pair after a reprice.
    """
    before = [game_winner("П1", 2.30), game_winner("П2", 1.47)]
    after = [game_winner("П1", 2.35), game_winner("П2", 1.45)]

    changes = diff_snapshots(before, after)

    assert len(changes) == 2
    by_outcome = {c.outcome: c for c in changes}
    assert (by_outcome["П1"].old, by_outcome["П1"].new) == (2.30, 2.35)
    assert (by_outcome["П2"].old, by_outcome["П2"].new) == (1.47, 1.45)
    assert all(c.repriced and not c.suspended for c in changes)
    assert all(c.was_active and c.is_active for c in changes)
    assert all(c.ref == MarketRef(scope="game", kind="Исход", set_no=2, game_no=7)
               for c in changes)


def test_a_bet_stop_lift_is_reported_though_the_price_did_not_move():
    """Match 5960849, set 1 game 1: fourteen game markets reopened in one step
    with every price exactly where it had been.

    This is the shape a price-only diff misses entirely, and it is the one the
    project most wants: `is_active` going down and back up is the bookmaker
    suspending and restoring a market, which is its repricing made visible.
    """
    before = [game_winner("П1", 1.52, is_active=False, game=1, set_=1)]
    after = [game_winner("П1", 1.52, is_active=True, game=1, set_=1)]

    (change,) = diff_snapshots(before, after)

    assert (change.was_active, change.is_active) == (False, True)
    assert not change.repriced
    assert not change.suspended
    assert change.old == change.new == 1.52


def test_a_suspension_keeps_the_price_and_is_not_a_departure():
    """Measured: the commonest factors on a closed stake are 100.0 and 1.01.

    `is_active=False` does not mean the price went to zero, so a diff must not
    read a zero into it -- and a suspended outcome is still on the board,
    which is what separates it from one that is gone.
    """
    before = [game_winner("П1", 2.30, is_active=True)]
    after = [game_winner("П1", 2.30, is_active=False)]

    (change,) = diff_snapshots(before, after)

    assert change.suspended
    assert not change.repriced
    assert change.new == 2.30
    assert change.is_active is False


def test_a_real_consecutive_pair():
    """Match 5950804, WTA 125 Porto, two snapshots 23.8s apart, 2026-09-22.

    The game markets of set 2 as they stood while game 8 was being played, and
    as they stood one snapshot later. Four things happened at once, and a
    capture that only logged "a snapshot arrived" showed none of them:

    * the 4th point of game 8 was played, so its market left the board;
    * the price on game 8 drifted out, 6.75 -> 8.25;
    * the other side of that game was suspended at an unchanged 1.03;
    * every market on game 9 -- the game actually being priced -- stood still.

    The last one is the reason to difference at all: in the full pair 25 of the
    29 game outcomes are noise, and the four that are not are the whole signal.
    """
    def board(fourth_point, winner_p1, winner_p2_active):
        stakes = [
            Stake("2-й сет 9-й гейм: Очки чёт/нечёт", "Чётный", 1.27),
            Stake("2-й сет 9-й гейм: Очки чёт/нечёт", "Нечётный", 3.35),
            Stake("2-й сет 9-й гейм: Точный счёт", "П1:15", 7.0),
            Stake("2-й сет 8-й гейм: Исход", "П1", winner_p1),
            Stake("2-й сет 8-й гейм: Исход", "П2", 1.03, winner_p2_active),
        ]
        if fourth_point:
            stakes += [Stake("2-й сет 8-й гейм: 4-е очко", "П1", 2.30),
                       Stake("2-й сет 8-й гейм: 4-е очко", "П2", 1.55)]
        return stakes

    changes = diff_snapshots(board(True, 6.75, True), board(False, 8.25, False))

    assert [(c.ref.game_no, c.ref.kind, c.outcome, c.old, c.new,
             c.was_active, c.is_active) for c in changes] == [
        (8, "4-е очко", "П1", 2.30, None, True, False),
        (8, "4-е очко", "П2", 1.55, None, True, False),
        (8, "Исход", "П1", 6.75, 8.25, True, True),
        (8, "Исход", "П2", 1.03, 1.03, True, False),
    ]
    assert [c.repriced for c in changes] == [False, False, True, False]
    assert [c.suspended for c in changes] == [False, False, False, True]


# --------------------------------------------------------------- shapes


def test_an_unchanged_board_produces_no_changes():
    """Measured: 4.3% of consecutive pairs are identical books."""
    board = [game_winner("П1", 2.30), game_winner("П2", 1.47)]
    assert diff_snapshots(board, list(board)) == []
    assert diff_snapshots([], []) == []


def test_an_outcome_that_was_not_there_before_is_an_arrival():
    """The book opens the next game: the outcome has no earlier quote."""
    (change,) = diff_snapshots([], [game_winner("П1", 2.30)])

    assert change.old is None and change.new == 2.30
    assert (change.was_active, change.is_active) == (False, True)
    assert not change.repriced


def test_an_outcome_that_is_gone_is_neither_repriced_nor_suspended():
    """The game finished: the outcome left the board altogether."""
    (change,) = diff_snapshots([game_winner("П1", 2.30)], [])

    assert change.old == 2.30 and change.new is None
    assert (change.was_active, change.is_active) == (True, False)
    assert not change.repriced
    assert not change.suspended


def test_a_change_carries_the_parsed_address_of_its_market():
    """Without the parsed address a change cannot be joined to a game."""
    before = [Stake("3-й сет 11-й гейм: Точный счёт", "П1:30", 5.1)]
    after = [Stake("3-й сет 11-й гейм: Точный счёт", "П1:30", 5.3)]

    (change,) = diff_snapshots(before, after)

    assert change.ref.is_game
    assert (change.ref.set_no, change.ref.game_no) == (3, 11)
    assert change.ref.kind == "Точный счёт"


def test_a_market_outside_a_game_still_diffs():
    """The module does not filter; a caller that wants games filters on `ref`."""
    before = [Stake("Фора по геймам", "П1", 1.85)]
    after = [Stake("Фора по геймам", "П1", 1.90)]

    (change,) = diff_snapshots(before, after)

    assert change.ref.scope == "match" and not change.ref.is_game
    assert change.repriced


def test_the_smallest_tick_is_a_move():
    """The feed quotes in discrete ticks; a tolerance would hide real moves."""
    (change,) = diff_snapshots([game_winner("П1", 1.45)],
                               [game_winner("П1", 1.46)])
    assert change.repriced


def test_order_is_by_market_address_then_outcome():
    """A diff read by a human, or compared between runs, needs a fixed order."""
    before = [game_winner("П1", 2.30, game=9),
              game_winner("П2", 1.47, game=7),
              game_winner("П1", 1.60, game=7)]
    after = [game_winner("П1", 2.40, game=9),
             game_winner("П2", 1.50, game=7),
             game_winner("П1", 1.55, game=7)]

    order = [(c.ref.game_no, c.outcome) for c in diff_snapshots(before, after)]

    assert order == [(7, "П1"), (7, "П2"), (9, "П1")]
    assert order == [(c.ref.game_no, c.outcome)
                     for c in diff_snapshots(list(reversed(before)),
                                             list(reversed(after)))]


def test_the_same_outcome_name_in_two_games_is_two_outcomes():
    """"П1" is the outcome name of every two-way market on the board.

    Only the market address separates them. Keyed on the outcome name alone, a
    diff would pair game 8's price with game 9's and invent a move on both.
    """
    before = [game_winner("П1", 6.75, game=8), game_winner("П1", 1.85, game=9)]
    after = [game_winner("П1", 8.25, game=8), game_winner("П1", 1.85, game=9)]

    (change,) = diff_snapshots(before, after)

    assert change.ref.game_no == 8


def test_order_holds_across_match_set_and_game_markets():
    """Off a game, `set_no` and `game_no` are None, which does not sort."""
    before = [Stake("2-й сет 9-й гейм: Точный счёт", "П1:15", 7.0),
              Stake("2-й сет 8-й гейм: Исход", "П2", 1.03),
              Stake("Исход", "П1", 1.50),
              Stake("2-й сет: Исход", "П1", 1.62)]
    after = [Stake("2-й сет 9-й гейм: Точный счёт", "П1:15", 7.5),
             Stake("2-й сет 8-й гейм: Исход", "П2", 1.04),
             Stake("Исход", "П1", 1.55),
             Stake("2-й сет: Исход", "П1", 1.66)]

    order = [(c.ref.scope, c.ref.set_no, c.ref.game_no, c.outcome)
             for c in diff_snapshots(before, after)]

    assert order == [("game", 2, 8, "П2"), ("game", 2, 9, "П1:15"),
                     ("match", None, None, "П1"), ("set", 2, None, "П1")]


def test_a_repeated_outcome_keeps_the_first_quote():
    """Never observed live: 176,995 stakes, zero repeated (market, outcome).

    It is pinned anyway because the alternative to a stated rule is a silent
    one: `_books` in `measure` already keeps the first of a repeated outcome,
    and a diff that disagreed with it would make two readings of one capture
    disagree for no visible reason.
    """
    before = [game_winner("П1", 2.30), game_winner("П1", 9.99)]
    after = [game_winner("П1", 2.35)]

    (change,) = diff_snapshots(before, after)

    assert change.old == 2.30


def test_snapshots_of_two_different_matches_are_refused():
    """The feed interleaves twenty matches; one `last_snapshot` is a trap.

    A diff across two matches is a full-board diff that looks plausible, so
    it is refused rather than returned.
    """
    before = [game_winner("П1", 2.30, match_id=5961556)]
    after = [game_winner("П1", 2.35, match_id=5951642)]

    with pytest.raises(ValueError, match="different matches"):
        diff_snapshots(before, after)


def test_the_same_match_on_both_sides_is_accepted():
    before = [game_winner("П1", 2.30, match_id=5961556)]
    after = [game_winner("П1", 2.35, match_id=5961556)]
    (change,) = diff_snapshots(before, after)
    assert change.repriced


def test_a_stake_without_a_match_id_does_not_trip_the_check():
    """Test doubles and older captures carry no match id; 0 is 'unknown'."""
    before = [game_winner("П1", 2.30, match_id=0)]
    after = [game_winner("П1", 2.35, match_id=5961556)]
    (change,) = diff_snapshots(before, after)
    assert change.repriced


def test_price_change_is_hashable_and_comparable():
    """Changes get collected, deduplicated and counted downstream."""
    ref = MarketRef(scope="game", kind="Исход", set_no=2, game_no=7)
    a = PriceChange(ref=ref, outcome="П1", old=2.30, new=2.35,
                    was_active=True, is_active=True)
    b = PriceChange(ref=ref, outcome="П1", old=2.30, new=2.35,
                    was_active=True, is_active=True)
    assert a == b
    assert len({a, b}) == 1


@pytest.mark.parametrize("old,new,repriced", [
    (2.30, 2.35, True),
    (2.30, 2.30, False),
    (None, 2.30, False),
    (2.30, None, False),
])
def test_repriced_means_two_quotes_that_both_exist_and_differ(old, new, repriced):
    """An arrival is not a move: there is no earlier price to have moved from."""
    change = PriceChange(ref=MarketRef(scope="match", kind="Исход"),
                         outcome="П1", old=old, new=new,
                         was_active=old is not None, is_active=new is not None)
    assert change.repriced is repriced
