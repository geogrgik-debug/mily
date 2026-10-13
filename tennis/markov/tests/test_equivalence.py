"""The lifted core must be numerically identical to the research original.

`research/elo_prior.py` produced the numbers in docs/EXPERIMENT_B: RMSE 0.0871
for the Elo inversion and 0.0855 for the blend on the 2022+ test. Re-running
that needs the Sackmann files, which are not in the repo, so the next best
regression guard is to prove the lift changed no arithmetic. If this file fails,
the documented results no longer describe `tennis/markov`.

`research/calc.py` cannot be imported at all -- it runs simulations at module
level -- so `p_hold_from` and `exact_score_dist` are instead pinned by the
closed-form and Monte Carlo tests in test_core.py.
"""
import importlib.util
import pathlib

import pytest

from tennis import markov

_ORIGINAL = pathlib.Path(__file__).resolve().parents[3] / "research" / "elo_prior.py"


def _load_original():
    if not _ORIGINAL.exists():
        pytest.skip(f"{_ORIGINAL} not present")
    spec = importlib.util.spec_from_file_location("_elo_prior_original", _ORIGINAL)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:                    # pandas/numpy absent
        pytest.skip(f"cannot import the original: {exc}")
    return module


@pytest.fixture(scope="module")
def original():
    return _load_original()


PS = [0.50, 0.532, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.75, 0.80]
PAIRS = [(a, b) for a in (0.55, 0.62, 0.70) for b in (0.55, 0.62, 0.70)]


@pytest.mark.parametrize("p", PS)
def test_p_game_identical(original, p):
    assert markov.p_game(p) == original.p_game(p)


@pytest.mark.parametrize("pa,pb", PAIRS)
def test_p_tiebreak_identical(original, pa, pb):
    assert markov.p_tiebreak(pa, pb) == pytest.approx(original.p_tiebreak(pa, pb), abs=1e-15)


@pytest.mark.parametrize("pa,pb", PAIRS)
def test_p_set_identical(original, pa, pb):
    assert markov.p_set(pa, pb) == pytest.approx(original.p_set(pa, pb), abs=1e-15)


@pytest.mark.parametrize("best_of", [3, 5])
@pytest.mark.parametrize("pa,pb", PAIRS)
def test_p_match_identical(original, pa, pb, best_of):
    assert markov.p_match(pa, pb, best_of) == pytest.approx(
        original.p_match(pa, pb, best_of), abs=1e-15
    )


@pytest.mark.parametrize("best_of", [3, 5])
@pytest.mark.parametrize("w", [0.05, 0.2, 0.35, 0.45, 0.5, 0.55, 0.65, 0.8, 0.95])
@pytest.mark.parametrize("baseline", [0.58, 0.62, 0.65])
def test_invert_identical(original, w, baseline, best_of):
    got = markov.invert(w, baseline, best_of)
    want = original.invert(w, baseline, best_of)
    assert got[0] == pytest.approx(want[0], abs=1e-15)
    assert got[1] == pytest.approx(want[1], abs=1e-15)
