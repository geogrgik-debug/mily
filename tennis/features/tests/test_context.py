"""The context is the scoreboard before the game, read the same way offline and live."""

import numpy as np
import pytest

from tennis.features import FEATURES, GameContext, matrix, vector
from tennis.model import GamePlay, build_game_rows


def _ctx(**kw):
    base = dict(level="tour", surface="Hard", best_of=3, set_no=1, server_games=0,
                returner_games=0, server_sets=0, returner_sets=0, served_before=0,
                just_broke=False, was_broken=False)
    return GameContext(**{**base, **kw})


def _named(c):
    return dict(zip(FEATURES, vector(c)))


def test_the_first_game_of_a_tour_match_on_hard_is_all_base():
    z = _named(_ctx())
    assert z["first_service_game"] == 1
    assert sum(v for k, v in z.items() if k != "first_service_game") == 0


def test_level_and_surface():
    z = _named(_ctx(level="slam", surface="Grass", best_of=5))
    assert (z["slam"], z["chall"], z["grass"], z["clay"]) == (1, 0, 1, 0)
    assert _named(_ctx(level="chall", surface="Carpet"))["chall"] == 1
    assert _named(_ctx(surface="Carpet"))["clay"] == _named(_ctx(surface="Carpet"))["grass"] == 0


def test_the_scoreboard():
    z = _named(_ctx(set_no=3, server_games=5, returner_games=4, server_sets=1,
                    returner_sets=1, served_before=12, just_broke=True))
    assert z["serving_for_set"] == 1 and z["serving_to_stay"] == 0
    assert z["deciding_set"] == 1 and z["sets_ahead"] == z["sets_behind"] == 0
    assert z["later_set"] == 2 and z["games_in_set"] == pytest.approx(0.9)
    assert z["service_games_so_far"] == pytest.approx(1.2) and z["first_service_game"] == 0
    assert z["just_broke"] == 1 and z["was_broken"] == 0
    z = _named(_ctx(set_no=2, server_games=3, returner_games=5, server_sets=0, returner_sets=1,
                    served_before=8, was_broken=True))
    assert z["serving_to_stay"] == 1 and z["sets_behind"] == 1 and z["deciding_set"] == 0
    assert z["was_broken"] == 1
    # the third set of a best-of-five is not the decider
    assert _named(_ctx(level="slam", best_of=5, set_no=3, server_sets=1, returner_sets=1))["deciding_set"] == 0


@pytest.mark.parametrize("server, returner, for_set, to_stay", [
    (5, 4, 1, 0), (4, 5, 0, 1), (6, 5, 1, 0), (5, 6, 0, 1), (5, 5, 0, 0), (6, 6, 0, 0),
    (4, 4, 0, 0), (5, 3, 1, 0), (3, 5, 0, 1),
])
def test_serving_for_the_set_and_to_stay_in_it(server, returner, for_set, to_stay):
    z = _named(_ctx(server_games=server, returner_games=returner, served_before=4))
    assert (z["serving_for_set"], z["serving_to_stay"]) == (for_set, to_stay)


def test_the_first_service_game_is_only_the_first():
    assert _named(_ctx(served_before=0))["first_service_game"] == 1
    assert _named(_ctx(served_before=1))["first_service_game"] == 0


def test_from_row_reads_what_game_rows_built():
    plays = [
        GamePlay(1, 2, (1, 1, 1, 1), set_no=2, server_games=0, returner_games=0,
                 server_sets=1, returner_sets=0),
        GamePlay(2, 1, (0, 0, 0, 0), set_no=2, server_games=0, returner_games=1,
                 server_sets=0, returner_sets=1),
        GamePlay(1, 2, (1, 1, 1, 1), set_no=2, server_games=2, returner_games=0,
                 server_sets=1, returner_sets=0),
    ]
    rows = build_game_rows("m", plays, lambda s, r: 0.64)
    c = GameContext.from_row(rows[2], "chall", "Clay", 3)
    assert (c.server_games, c.returner_games, c.server_sets, c.returner_sets) == (2, 0, 1, 0)
    assert c.served_before == 1 and c.just_broke and not c.was_broken
    c = GameContext.from_row(rows[1], "chall", "Clay", 3)
    assert (c.server_games, c.returner_games) == (0, 1) and not c.just_broke


def test_matrix_stacks_vectors_and_keeps_its_width_when_empty():
    cs = [_ctx(), _ctx(level="slam", best_of=5)]
    assert np.array_equal(matrix(cs), np.vstack([vector(c) for c in cs]))
    assert matrix([]).shape == (0, len(FEATURES))
    assert len(vector(_ctx())) == len(FEATURES)


@pytest.mark.parametrize("bad", [dict(level="itf"), dict(best_of=4), dict(set_no=0),
                                 dict(server_games=-1)])
def test_nonsense_is_refused(bad):
    with pytest.raises(ValueError):
        _ctx(**bad)
