"""Rows are built from the past only, and the target is the real game winner.

The leak test is the point of the module: poison every game after the k-th and
the first k rows must not move. Because the builder streams -- a game folds into
the accumulators only after its row is out -- this holds by construction; the
test keeps a later refactor honest.
"""

import random
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tennis.model import (
    GamePlay, GameRow, build_game_rows, game_winner, prior_for_match, prior_for_pair,
)
from tennis.model.game_rows import poison_future
from tennis.state import DEFAULT_N0, ServeBelief


# ---- game_winner: the target must be the real winner, not an approximation ----

def test_love_hold_and_love_break():
    assert game_winner((1, 1, 1, 1)) == 1
    assert game_winner((0, 0, 0, 0)) == 0


def test_deuce_battles_resolve_correctly():
    # 40-40, server takes advantage and the game
    assert game_winner((1, 1, 0, 0, 1, 1)) == 1
    # 40-40, returner breaks after two deuces
    assert game_winner((1, 1, 0, 0, 1, 0, 0, 0)) == 0


def test_a_long_deuce_needs_the_two_point_margin():
    pts = (1, 1, 0, 0) + (1, 0) * 5 + (1, 1)   # many deuces, then server wins
    assert game_winner(pts) == 1


def test_an_unfinished_game_is_an_error():
    with pytest.raises(ValueError):
        game_winner((1, 1, 1))          # only 40-0, not over
    with pytest.raises(ValueError):
        game_winner((1, 1, 0, 0))       # deuce, not over


def test_a_point_is_one_or_zero():
    with pytest.raises(ValueError):
        game_winner((1, 2, 1, 1))


# ---- a hand-checked match --------------------------------------------------

def _flat_prior(server, returner):
    return 0.62


def _match():
    # A serves games 0 and 2, B serves games 1 and 3. A holds both; B holds then
    # is broken. Set score before each game is supplied as the feed would.
    return [
        GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=1,
                 server_games=0, returner_games=0),   # A holds to love
        GamePlay(server=2, returner=1, points=(1, 1, 1, 1), set_no=1,
                 server_games=0, returner_games=1),   # B holds to love
        GamePlay(server=1, returner=2, points=(1, 1, 0, 1, 1), set_no=1,
                 server_games=1, returner_games=1),   # A holds, dropped one
        GamePlay(server=2, returner=1, points=(0, 0, 1, 0, 0), set_no=1,
                 server_games=1, returner_games=2),   # B broken
    ]


def test_targets_and_the_first_game_starts_empty():
    rows = build_game_rows("m1", _match(), _flat_prior)
    assert [r.hold for r in rows] == [1, 1, 1, 0]

    first = rows[0]
    assert first.cum_serve_points == 0 and first.cum_serve_won == 0
    assert first.cum_games == 0 and first.has_prev == 0 and first.prev_hold == -1
    # with no history, live_spw is exactly the prior
    assert first.live_spw == pytest.approx(0.62)


def test_cumulative_is_strictly_before_this_game():
    rows = build_game_rows("m1", _match(), _flat_prior)
    a_second = rows[2]        # A's second service game (game_no 2)
    # A's first game only: 4 points, 4 won, 1 hold
    assert a_second.cum_serve_points == 4
    assert a_second.cum_serve_won == 4
    assert a_second.cum_games == 1 and a_second.cum_holds == 1
    assert a_second.has_prev == 1 and a_second.prev_hold == 1
    assert a_second.prev_easy_hold == 1 and a_second.prev_hard_hold == 0


def test_the_returners_own_serve_is_tracked_separately():
    rows = build_game_rows("m1", _match(), _flat_prior)
    # game_no 3: B serves, A returns. B has served one game (game_no 1, a hold).
    b_second = rows[3]
    assert b_second.server == 2 and b_second.returner == 1
    assert b_second.cum_games == 1 and b_second.cum_holds == 1
    # the opponent here is A, who has held twice by now
    assert b_second.opp_cum_games == 2 and b_second.opp_cum_serve_points == 9


def test_context_reads_the_set_score():
    g = GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=2,
                 server_games=5, returner_games=3)
    row = build_game_rows("m", [g], _flat_prior)[0]
    assert row.game_diff == 2
    assert row.serving_for_set == 1 and row.serving_to_stay == 0
    assert row.games_in_set == 8 and row.set_no == 2


def test_serving_to_stay_in_the_set():
    g = GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=1,
                 server_games=3, returner_games=5)
    row = build_game_rows("m", [g], _flat_prior)[0]
    assert row.serving_to_stay == 1 and row.serving_for_set == 0


# ---- the leak test ---------------------------------------------------------

def test_poisoning_every_later_game_leaves_earlier_rows_untouched():
    games = _match()
    full = build_game_rows("m1", games, _flat_prior)
    for keep in range(1, len(games)):
        poisoned = build_game_rows("m1", poison_future(games, keep), _flat_prior)
        assert poisoned[:keep] == full[:keep], f"row {keep - 1} moved when the future changed"


def test_a_randomised_future_cannot_reach_back():
    rng = random.Random(0)

    def rand_game(server, returner, sg, rg):
        # a random but completed game
        pts = []
        s = r = 0
        while True:
            p = rng.randint(0, 1)
            pts.append(p)
            s, r = s + (p == 1), r + (p == 0)
            if (s >= 4 and s - r >= 2) or (r >= 4 and r - s >= 2):
                break
        return GamePlay(server, returner, tuple(pts), 1, sg, rg)

    base = [rand_game(1 if i % 2 == 0 else 2, 2 if i % 2 == 0 else 1, i // 2, i // 2)
            for i in range(12)]
    full = build_game_rows("r", base, _flat_prior)
    for keep in range(1, len(base)):
        tail = [rand_game(1 if i % 2 == 0 else 2, 2 if i % 2 == 0 else 1, 0, 0)
                for i in range(keep, len(base))]
        mixed = base[:keep] + tail
        assert build_game_rows("r", mixed, _flat_prior)[:keep] == full[:keep]


def test_each_players_serve_is_shrunk_toward_his_own_serve_prior():
    seen = []
    priors = {(1, 2): 0.70, (2, 1): 0.64}

    def prior_for(server, returner):
        seen.append((server, returner))
        return priors[(server, returner)]

    g = GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=1,
                 server_games=0, returner_games=0)
    row = build_game_rows("m", [g], prior_for)[0]
    assert seen == [(1, 2), (2, 1)]
    assert row.live_spw == pytest.approx(0.70)
    # the returner, from an empty history, sits at the prior of his own serve --
    # not at 1 - 0.70, which is his chance on return
    assert row.opp_live_spw == pytest.approx(0.64)


def _belief_mean(p, n, won):
    b = ServeBelief.from_prior(p, DEFAULT_N0)
    for w in [1] * won + [0] * (n - won):
        b = b.observe(w)
    return b.mean


def test_live_spw_is_the_live_states_belief_at_the_fitted_strength():
    # A serves 21 points (12 won) over five games, B serves 21 (8 won); the
    # sixth game's row must hold both beliefs at n0 = DEFAULT_N0, so a revert
    # of the constant (it was 40) fails here.
    priors = {(1, 2): 0.66, (2, 1): 0.62}
    a_games = [(1, 1, 1, 1), (1, 1, 1, 1), (0, 0, 0, 0), (0, 0, 0, 0), (1, 1, 0, 1, 1)]
    b_games = [(1, 1, 1, 1), (0, 0, 0, 0), (0, 0, 0, 0), (1, 0, 1, 1, 1), (0, 0, 0, 0)]
    games = []
    for ga, gb in zip(a_games, b_games):
        games.append(GamePlay(server=1, returner=2, points=ga, set_no=1,
                              server_games=0, returner_games=0))
        games.append(GamePlay(server=2, returner=1, points=gb, set_no=1,
                              server_games=0, returner_games=0))
    games.append(GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=1,
                          server_games=0, returner_games=0))
    row = build_game_rows("m", games, lambda s, r: priors[(s, r)])[-1]
    assert (row.cum_serve_points, row.cum_serve_won) == (21, 12)
    assert (row.opp_cum_serve_points, row.opp_cum_serve_won) == (21, 8)
    assert row.live_spw == pytest.approx((12 + DEFAULT_N0 * 0.66) / (21 + DEFAULT_N0))
    assert row.opp_live_spw == pytest.approx((8 + DEFAULT_N0 * 0.62) / (21 + DEFAULT_N0))
    assert row.live_spw == pytest.approx(_belief_mean(0.66, 21, 12))
    assert row.opp_live_spw == pytest.approx(_belief_mean(0.62, 21, 8))


def test_rows_are_frozen():
    row = build_game_rows("m1", _match(), _flat_prior)[0]
    with pytest.raises(Exception):
        row.hold = 0          # type: ignore[misc]
    assert isinstance(row, GameRow)


# ---- sets and the break just before ----------------------------------------

def test_sets_won_are_carried_from_the_play():
    g = GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=3,
                 server_games=2, returner_games=1, server_sets=0, returner_sets=2)
    row = build_game_rows("m", [g], _flat_prior)[0]
    assert (row.server_sets, row.returner_sets) == (0, 2)


def test_the_server_just_broke_when_the_game_before_was_a_break():
    # B is broken in the last game of _match; A serves next.
    games = _match() + [GamePlay(server=1, returner=2, points=(1, 1, 1, 1), set_no=1,
                                 server_games=3, returner_games=1)]
    rows = build_game_rows("m1", games, _flat_prior)
    assert [r.server_just_broke for r in rows] == [0, 0, 0, 0, 1]
    # the break is the returner's game lost, not the server's own last game
    assert rows[4].prev_broken == 0


def test_no_break_carries_over_a_tie_break():
    # 6-6, a tie-break (not a service game, so not in the list), then set 2 opens
    # with the player who served the last game before it: that game is his own,
    # not a break he made, even though he lost it.
    before_tb = GamePlay(server=2, returner=1, points=(0, 0, 0, 0), set_no=1,
                         server_games=5, returner_games=6)
    set2 = GamePlay(server=2, returner=1, points=(1, 1, 1, 1), set_no=2,
                    server_games=0, returner_games=0, server_sets=0, returner_sets=1)
    rows = build_game_rows("m", [before_tb, set2], _flat_prior)
    assert rows[1].server_just_broke == 0 and rows[1].prev_broken == 1


# ---- one prior per match ---------------------------------------------------

def test_prior_for_pair_reads_both_sides_of_one_prior():
    pf = prior_for_pair(7, 9, 0.68, 0.63)
    assert pf(7, 9) == 0.68 and pf(9, 7) == 0.63
    with pytest.raises(ValueError):
        pf(7, 8)


def test_prior_for_match_asks_the_snapshot_once_with_one_date_and_surface():
    calls = []

    class Snap:
        def prior(self, a, b, surface, best_of, as_of=None):
            calls.append((a, b, surface, best_of, as_of))
            return SimpleNamespace(p_serve_a=0.66, p_serve_b=0.61)

    pf = prior_for_match(Snap(), 1, 2, "Clay", 3, "2026-09-21")
    g = GamePlay(server=2, returner=1, points=(1, 1, 1, 1), set_no=1,
                 server_games=0, returner_games=0)
    row = build_game_rows("m", [g, replace(g, server=1, returner=2)], pf)
    assert calls == [(1, 2, "Clay", 3, "2026-09-21")]
    assert row[0].prior_p_serve == 0.61 and row[1].prior_p_serve == 0.66
