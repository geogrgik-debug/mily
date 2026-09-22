"""A snapshot is the offline job's product: it must reload exactly, extend
exactly, agree with the batch priors, and know nothing of what came after it."""
import numpy as np
import pytest

from tennis.ratings import RatingsSnapshot, SWEEP_BEST, build_elo, from_records
from tennis.ratings.serve import match_entries, rolling_windows
from tennis.ratings.tests.synthetic import generate

SPLIT = "2021-07-05"      # a Monday: tournaments start on it in the synthetic files


@pytest.fixture(scope="module")
def matches():
    return from_records(generate(years=range(2019, 2023)))


@pytest.fixture(scope="module")
def snap(matches):
    return RatingsSnapshot.build(matches, SWEEP_BEST, as_of=SPLIT, sources={"x.csv": "sha"})


def _add_all(snapshot, m):
    for r in range(len(m)):
        snapshot.add_result(
            date=m.date[r], winner=m.winner_id[r], loser=m.loser_id[r], surface=m.surface[r],
            level=m.tourney_level[r],
            w_stats=(m.w_svpt[r], m.w_1stWon[r], m.w_2ndWon[r]),
            l_stats=(m.l_svpt[r], m.l_1stWon[r], m.l_2ndWon[r]),
            winner_name=m.winner_name[r], loser_name=m.loser_name[r])


def test_save_and_load_reproduce_the_snapshot(snap, tmp_path):
    for name in ("s.json", "s.json.gz"):
        snap.save(tmp_path / name)
        back = RatingsSnapshot.load(tmp_path / name)
        assert back == snap
        a, b = sorted(snap.elo.count)[:2]
        assert back.prior(a, b, "Clay", 3, SPLIT) == snap.prior(a, b, "Clay", 3, SPLIT)


def test_extending_result_by_result_equals_rebuilding(matches, snap, tmp_path):
    snap.save(tmp_path / "s.json")
    extended = RatingsSnapshot.load(tmp_path / "s.json")
    later = matches.take(np.flatnonzero(matches.date >= np.datetime64(SPLIT)))
    _add_all(extended, later)
    rebuilt = RatingsSnapshot.build(matches, SWEEP_BEST, sources={"x.csv": "sha"})
    assert extended == rebuilt


def test_nothing_after_the_as_of_date_leaks_in(matches):
    # Poison every tournament from the split on: other winners, other stats.
    rng = np.random.default_rng(3)
    poisoned = matches.take(np.arange(len(matches)))
    late = poisoned.date >= np.datetime64(SPLIT)
    swap = late & (rng.random(len(poisoned)) < 0.5)
    poisoned.winner_id[swap], poisoned.loser_id[swap] = (
        poisoned.loser_id[swap].copy(), poisoned.winner_id[swap].copy())
    poisoned.w_svpt[late] = rng.integers(40, 120, late.sum())
    poisoned.surface[late] = "Grass"
    clean = RatingsSnapshot.build(matches, SWEEP_BEST, as_of=SPLIT)
    dirty = RatingsSnapshot.build(poisoned, SWEEP_BEST, as_of=SPLIT)
    assert clean == dirty
    ids = sorted(clean.elo.count)
    for a, b in zip(ids[:10], ids[10:20]):
        assert clean.prior(a, b, "Hard", 3, SPLIT) == dirty.prior(a, b, "Hard", 3, SPLIT)


def test_it_agrees_with_the_batch_on_the_next_tournament_day(matches, snap):
    # The batch computes each match's inputs as of that match; the snapshot as
    # of its date. On the first date after the snapshot they must coincide:
    # every serve window that day, and the Elo expectancy of the day's first
    # match (later ones already see the day's earlier results, as in B1).
    elo, _ = build_elo(matches, SWEEP_BEST)
    pids, rows, won, entries = match_entries(matches)
    win = rolling_windows(pids, entries, SWEEP_BEST.window_days, True)
    on_split = np.flatnonzero(matches.date[rows] == np.datetime64(SPLIT))
    assert len(on_split) > 10
    for i in on_split:
        assert snap.serve.window_day(int(pids[i]), entries[i].date)[:4] == tuple(win[i, :4])
    first = int(np.flatnonzero(matches.date == np.datetime64(SPLIT))[0])
    pr = snap.prior(int(matches.winner_id[first]), int(matches.loser_id[first]),
                    matches.surface[first], 3, SPLIT)
    assert pr.win_prob_a == elo[first, 2]


def test_the_prior_is_the_configured_blend(snap):
    a, b = sorted(snap.elo.count, key=lambda p: -snap.elo.blended(p, "Hard"))[:2]
    pr = snap.prior(a, b, "Hard", 5, SPLIT)
    w = SWEEP_BEST.blend_elo
    assert pr.p_serve_a == w * pr.p_elo_a + (1 - w) * pr.p_bc_a
    assert pr.p_serve_b == w * pr.p_elo_b + (1 - w) * pr.p_bc_b
    assert pr.win_prob_a > 0.5 and pr.p_elo_a > pr.p_elo_b
    # the inverted pair averages the (rounded) surface baseline
    assert (pr.p_elo_a + pr.p_elo_b) / 2 == pytest.approx(pr.baseline, abs=1e-3)


def test_an_unknown_player_gets_the_initial_rating_and_an_empty_window(snap):
    known = next(iter(snap.elo.count))
    pr = snap.prior(999999, known, "Hard", 3, SPLIT)
    assert pr.matches_a == 0 and pr.window_points_a == 0.0
    assert pr.elo_a == SWEEP_BEST.initial_rating


def test_it_refuses_what_it_cannot_answer_honestly(snap):
    a, b = sorted(snap.elo.count)[:2]
    with pytest.raises(ValueError, match="before the snapshot"):
        snap.prior(a, b, "Hard", 3, "2021-06-01")
    snap.prior(a, b, "Hard", 3, snap.last_date)        # the same date is allowed
    with pytest.raises(ValueError, match="surface"):
        snap.prior(a, b, "Hardcourt", 3, SPLIT)
    with pytest.raises(ValueError, match="best_of"):
        snap.prior(a, b, "Hard", 4, SPLIT)
    with pytest.raises(ValueError, match="as_of"):
        snap.prior(a, b, "Hard", 3)
    with pytest.raises(ValueError, match="older"):
        snap.add_result(date="2020-01-06", winner=a, loser=b, surface="Hard")


def test_it_keeps_only_what_a_future_window_can_reach(matches, snap):
    last = np.datetime64(snap.last_date)
    assert snap.last_date < SPLIT
    before = matches.before(SPLIT)
    recent = before.date >= last - np.timedelta64(SWEEP_BEST.window_days, "D")
    assert 0 < len(snap.serve) <= 2 * int(recent.sum())


def test_elo_inside_equals_the_batch_elo(matches, snap):
    _, st = build_elo(matches.before(SPLIT), SWEEP_BEST)
    assert snap.elo == st


def test_players_can_be_found_by_name(snap):
    pid = next(iter(snap.names))
    assert snap.find(snap.names[pid]) == [pid]
    assert snap.find(snap.names[pid].upper()) == [pid]
    assert len(snap.find("Player")) == len(snap.names)
