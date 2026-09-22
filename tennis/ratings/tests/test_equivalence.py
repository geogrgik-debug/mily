"""The lift must compute what `research/elo_prior.py` computed.

The B1 and B1c numbers in docs/EXPERIMENT_B came from that script. The real
match files cannot ship with the repository, so these tests run both sides on
synthetic files in Sackmann's format and demand the same answer: row order,
the Elo bit for bit, every serve window, and the priors row for row.

On the real files (2026-09-22, mirror through 2026-06-01) the same comparison
gave: loading identical for 270,662 matches; Elo bit-identical under both
configs; serve windows identical except 652 of 411,124, where one player has
two events starting the same day and the original's unstable sort decided the
order; every B1 RMSE equal to four decimals. See tennis/ratings/README.md.

Skipped when `research/` or pandas is absent -- a capture host has neither.
"""
import importlib.util
import pathlib

import numpy as np
import pytest

from tennis.ratings import RESEARCH_B1, SWEEP_BEST, build_elo, historical_priors, load_matches
from tennis.ratings.elo import PRE_MATCH
from tennis.ratings.serve import match_entries, rolling_windows, tour_mean
from tennis.ratings.tests.synthetic import generate, write_files

_ORIGINAL = pathlib.Path(__file__).resolve().parents[3] / "research" / "elo_prior.py"


@pytest.fixture(scope="module")
def original():
    if not _ORIGINAL.exists():
        pytest.skip(f"{_ORIGINAL} not present")
    pytest.importorskip("pandas")
    spec = importlib.util.spec_from_file_location("_elo_prior_original_r", _ORIGINAL)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        pytest.skip(f"cannot import the original: {exc}")
    return module


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("atp")
    write_files(d, generate())
    return d


@pytest.fixture(scope="module")
def small_files(tmp_path_factory):
    # Inverting every row is the slow part; three years still straddle the
    # original's 2021/2022 train-test split.
    d = tmp_path_factory.mktemp("atp_small")
    write_files(d, generate(seed=11, years=range(2020, 2023), weeks=16))
    return d


@pytest.fixture(scope="module")
def both(original, files):
    return load_matches(files), original.load_matches(str(files))


def _same_floats(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return np.array_equal(np.isnan(a), np.isnan(b)) and np.array_equal(a[~np.isnan(a)], b[~np.isnan(b)])


def test_loading_matches_the_original_row_for_row(both):
    mine, theirs = both
    assert len(mine) == len(theirs) > 7000
    assert list(mine.match_key) == list(theirs["match_key"])
    assert np.array_equal(mine.winner_id, theirs["winner_id"].to_numpy().astype(np.int64))
    assert np.array_equal(mine.loser_id, theirs["loser_id"].to_numpy().astype(np.int64))
    assert np.array_equal(mine.date, theirs["date"].to_numpy().astype("datetime64[D]"))
    assert list(mine.surface) == list(theirs["surface"])
    assert np.array_equal(mine.best_of, theirs["best_of"].to_numpy())
    assert list(mine.src) == list(theirs["src"])
    for c in ("w_svpt", "w_1stWon", "w_2ndWon", "l_svpt", "l_1stWon", "l_2ndWon"):
        assert _same_floats(getattr(mine, c), theirs[c]), c


@pytest.mark.parametrize("config,kwargs", [
    (RESEARCH_B1, {}),
    (SWEEP_BEST, dict(K0=400.0, Kexp=0.4, surface_weight=0.3)),
], ids=["research-b1", "sweep"])
def test_elo_is_bit_identical(original, both, config, kwargs):
    mine, theirs = both
    pre, state = build_elo(mine, config)
    t, overall, surf, cnt = original.build_elo(theirs.copy(), **kwargs)
    for j, c in enumerate(PRE_MATCH):
        assert np.array_equal(pre[:, j], t[c].to_numpy()), c
    assert state.overall == {int(k): v for k, v in overall.items()}
    assert state.surface == {(int(k[0]), k[1]): v for k, v in surf.items()}
    assert state.count == {int(k): v for k, v in cnt.items()}


def test_serve_windows_match_the_original(original, both):
    mine, theirs = both
    pids, rows, _, entries = match_entries(mine)
    win = rolling_windows(pids, entries, SWEEP_BEST.window_days, True)
    _, R, tour = original.rolling_priors(theirs)
    assert tour == tour_mean(entries)
    got = {(int(p), k): tuple(w) for p, k, w in zip(pids, mine.match_key[rows], win)}
    assert len(got) == len(R)
    for r in R.itertuples():
        assert got[(int(r.pid), r.match_key)] == (
            r.spw_k_w, r.spw_n_w, r.rpw_k_w, r.rpw_n_w, r.n_prev_matches), (r.pid, r.match_key)


def test_priors_match_the_original_row_for_row(original, small_files, tmp_path, capsys):
    import pandas as pd
    res = original.evaluate(str(small_files), str(tmp_path / "r.json"), str(tmp_path / "priors.csv"))
    theirs = pd.read_csv(tmp_path / "priors.csv", float_precision="round_trip")
    m = load_matches(small_files)
    h = historical_priors(m, RESEARCH_B1)
    assert len(h["pid"]) == len(theirs)
    assert list(h["pid"]) == list(theirs["pid"])
    assert list(m.match_key[h["row"]]) == list(theirs["match_key"])
    assert np.array_equal(h["win_prob"], theirs["win_prob"].to_numpy())
    assert np.array_equal(h["baseline"], theirs["baseline"].to_numpy())
    assert np.array_equal(h["spw_raw"], theirs["p_raw"].to_numpy())
    assert np.array_equal(h["window_points"], theirs["spw_n_w"].to_numpy())
    assert np.array_equal(h["p_elo"], theirs["p_elo"].to_numpy())
    assert np.array_equal(h["p_bc"], theirs["p_bc"].to_numpy())
    w = res["blend_weight_elo"]
    assert np.array_equal(w * h["p_elo"] + (1 - w) * h["p_bc"], theirs["p_blend"].to_numpy())
