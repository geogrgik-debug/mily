"""Step 5's machinery on simulated matches: set scores right, no future in a
row, no test year in the fit, and the fits find what was planted."""

import numpy as np
import pytest

import tennis.eval.calibrate as calibrate_mod
from tennis.eval.calibrate import (
    FEATURES, GROUPS, Rows, beta_apply, build_rows, calibration_stats, choose_calibration,
    choose_l2, ece, equal_count_edges, evaluate, fit_params, isotonic_apply, isotonic_fit,
    match_folds, prefer_per_level, role_of, set_numbers, to_plays,
)
from tennis.eval.join import PricedMatch
from tennis.eval.live_state import log_loss
from tennis.eval.points import Game, MatchPoints, valid_game
from tennis.eval.set_marks import compare, pbp_marks, slam_marks
from tennis.features import GameContext, vector
from tennis.model.hold import expit, logit, p_hold, predict, start_state
from tennis.state import N0, MatchState


# ---- simulated matches with real set structure --------------------------------

def _game(rng, server, p):
    pts, a, b = [], 0, 0
    while not (max(a, b) >= 4 and abs(a - b) >= 2):
        w = int(rng.random() < p[server])
        pts.append((server, w))
        a, b = a + w, b + 1 - w
    return Game(server, tuple(pts))


def _tiebreak(rng, first, p):
    pts, won, n = [], {1: 0, 2: 0}, 0
    while not (max(won.values()) >= 7 and abs(won[1] - won[2]) >= 2):
        srv = first if (n + 1) // 2 % 2 == 0 else 3 - first
        w = int(rng.random() < p[srv])
        pts.append((srv, w))
        won[srv if w else 3 - srv] += 1
        n += 1
    g = Game(first, tuple(pts), tiebreak=True)
    assert valid_game(g)
    return g


def sim_match(rng, key, year=2013, level="tour", p=None):
    p = p or {1: float(rng.uniform(0.58, 0.70)), 2: float(rng.uniform(0.58, 0.70))}
    games, sets, server = [], {1: 0, 2: 0}, 1
    while max(sets.values()) < 2:
        g = {1: 0, 2: 0}
        while True:
            game = _tiebreak(rng, server, p) if g[1] == g[2] == 6 else _game(rng, server, p)
            games.append(game)
            server = 3 - server
            g[game.winner()] += 1
            if game.tiebreak or (max(g.values()) >= 6 and abs(g[1] - g[2]) >= 2):
                sets[game.winner()] += 1
                break
    m = MatchPoints(key=key, source="pbp", level=level, year=year, date="", event="",
                    name1="a", name2="b", games=tuple(games))
    return PricedMatch(match=m, date="", surface="Clay" if year % 2 else "Hard", best_of=3,
                       p1=p[1], p2=p[2], rated1=50, rated2=50)


def sim_priced(n, seed=0):
    rng = np.random.default_rng(seed)
    years = (2011, 2012, 2013, 2014, 2015, 2017)
    return [sim_match(rng, str(k), years[k % len(years)], ("tour", "chall")[k // len(years) % 2])
            for k in range(n)]


# ---- set scores --------------------------------------------------------------

def _hold(server, lose=False):
    return Game(server, ((server, 0 if lose else 1),) * 4)


def test_to_plays_counts_games_and_sets_with_a_tie_break_between():
    games = []
    for i in range(12):                                    # 6-6, every game held
        games.append(_hold(1 + i % 2))
    servers = [1 if (n + 1) // 2 % 2 == 0 else 2 for n in range(7)]
    tb = tuple((srv, int(srv == 1)) for srv in servers)
    games.append(Game(1, tb, tiebreak=True))               # player 1 takes it 7-0
    games.append(_hold(2))                                 # set 2 opens with player 2
    plays = to_plays(MatchPoints("k", "pbp", "tour", 2013, "", "", "a", "b", tuple(games)))
    assert Game(1, tb, tiebreak=True).winner() == 1
    assert len(plays) == 13
    assert (plays[11].set_no, plays[11].server_games, plays[11].returner_games) == (1, 5, 6)
    last = plays[12]
    assert (last.set_no, last.server, last.server_games, last.returner_games) == (2, 2, 0, 0)
    assert (last.server_sets, last.returner_sets) == (0, 1)


def test_an_advantage_final_set_runs_past_six_all():
    games = []
    for i in range(12):                                    # a first set of held serves...
        games.append(_hold(1 + i % 2, lose=(i == 11)))     # ...broken at 6-5: 7-5 to player 1
    for i in range(14):                                    # the next "set" goes 7-7 on serve
        games.append(_hold(1 + i % 2))
    plays = to_plays(MatchPoints("k", "slam", "slam", 2013, "", "", "a", "b", tuple(games)))
    assert plays[12].set_no == 2 and plays[12].server_sets == 1
    assert plays[-1].set_no == 2 and plays[-1].server_games + plays[-1].returner_games == 13


def test_the_ablation_groups_split_the_features_exactly():
    grouped = [f for cols in GROUPS.values() for f in cols]
    assert sorted(grouped) == sorted(FEATURES) and len(grouped) == len(set(grouped))


def test_roles_follow_the_years():
    def m(source, year):
        return MatchPoints("k", source, "slam" if source == "slam" else "tour", year, "", "",
                           "a", "b", ())
    assert [role_of(m("slam", y)) for y in (2012, 2014, 2015, 2016, 2017, 2018, 2019, 2024)] == \
        ["fit", "fit", "cal", "cal", "", "", "test", "test"]
    assert [role_of(m("pbp", y)) for y in (2011, 2014, 2015, 2016, 2017)] == \
        ["fit", "fit", "cal", "", "test"]


# ---- the rows ------------------------------------------------------------------

def test_rows_carry_the_live_state_at_each_levels_n0_and_the_scoreboard():
    priced = sim_priced(6, seed=1)
    rows = build_rows(priced)
    assert rows.Z.shape == (len(rows.hold), len(FEATURES))
    at = 0
    for mi, pm in enumerate(priced):
        s = MatchState.start(1, 2, pm.p1, pm.p2, N0[pm.match.level])
        for g in pm.match.games:
            if not g.tiebreak:
                assert rows.match[at] == mi and rows.level[at] == pm.match.level
                assert rows.h[at] == pytest.approx(s.p_hold_next(g.server), abs=1e-12)
                at += 1
            for srv, won in g.points:
                s = s.after_point(srv, won)
    assert at == len(rows.hold)


def test_poisoning_later_games_leaves_earlier_rows_alone():
    pm = sim_priced(1, seed=2)[0]
    honest = build_rows([pm])
    games = pm.match.games
    regular = [i for i, g in enumerate(games) if not g.tiebreak]
    for cut in (1, 5, 11, len(regular) - 1):
        k = regular[cut]                                   # keep games[:k]; k is a service game
        fake = tuple(Game(g.server, ((g.server, 0),) * 4) if not g.tiebreak else g
                     for g in games[k:])
        m2 = MatchPoints(**{**pm.match.__dict__, "games": games[:k] + fake})
        poisoned = build_rows([PricedMatch(**{**pm.__dict__, "match": m2})])
        n = cut                                            # rows before game k
        assert np.array_equal(poisoned.Z[:n + 1], honest.Z[:n + 1])   # row k's context is pre-game
        assert np.array_equal(poisoned.h[:n + 1], honest.h[:n + 1])
        assert np.array_equal(poisoned.opp_dev[:n + 1], honest.opp_dev[:n + 1])


def _lost(play):
    return 2 * sum(play.points) < len(play.points)


def _honest_context(pm, plays, i):
    """The scoreboard before service game i, counted from the plays by hand."""
    g = plays[i]
    own = [p for p in plays[:i] if p.server == g.server]
    return GameContext(level=pm.match.level, surface=pm.surface, best_of=pm.best_of,
                       set_no=g.set_no, server_games=g.server_games,
                       returner_games=g.returner_games, server_sets=g.server_sets,
                       returner_sets=g.returner_sets, served_before=len(own),
                       just_broke=i > 0 and plays[i - 1].server == g.returner and _lost(plays[i - 1]),
                       was_broken=bool(own) and _lost(own[-1]))


def test_the_context_of_a_row_is_the_scoreboard_before_it():
    pm = sim_priced(1, seed=3)[0]
    rows = build_rows([pm])
    plays = to_plays(pm.match)
    assert len(plays) == len(rows.hold)
    for i in range(len(plays)):
        assert np.array_equal(rows.Z[i], vector(_honest_context(pm, plays, i))), f"row {i}"


# ---- the fit: roles only, and it finds what was planted --------------------------

def _synthetic_rows(n=150_000, seed=0, planted=None):
    rng = np.random.default_rng(seed)
    k = len(FEATURES)
    Z = rng.integers(0, 2, (n, k)).astype(float)
    planted = np.zeros(k) if planted is None else planted
    h = rng.uniform(0.55, 0.95, n)
    y = (rng.random(n) < expit(logit(h) + 0.03 + Z @ planted)).astype(np.int64)
    match = np.arange(n) // 10
    role = np.array(["fit", "fit", "fit", "cal", "test", ""])[match % 6]
    return Rows(match=match, level=np.array(["tour"] * n), role=role, hold=y, prior=h, h=h,
                Z=Z, opp_dev=np.zeros(n))


def test_the_fit_finds_a_planted_context_effect_and_leaves_calibration_alone():
    planted = np.zeros(len(FEATURES))
    planted[FEATURES.index("serving_for_set")] = -0.25
    planted[FEATURES.index("chall")] = 0.15
    params = fit_params(_synthetic_rows(planted=planted), l2_grid=(0.0, 100.0))
    assert np.array(params.coef) == pytest.approx(planted, abs=0.05)
    assert params.intercept == pytest.approx(0.03, abs=0.05)
    # the model is right, so the calibration map on top is close to the identity
    # (25 000 calibration rows: about 0.01 of noise at the thin ends)
    grid = np.linspace(0.55, 0.97, 50)
    assert np.abs(beta_apply(grid, params.calibration["tour"]) - grid).max() < 0.02


def test_a_level_the_residual_cannot_fix_gets_its_own_map():
    # Challenger's truth is steeper than the residual can say (a slope, not a
    # shift a level feature would absorb): the calibration years must pick a map
    # per level, and that map, not the common one, must reach the parameters
    rng = np.random.default_rng(12)
    n = 200_000
    Z = np.zeros((n, len(FEATURES)))
    chall = rng.random(n) < 0.5
    Z[chall, FEATURES.index("chall")] = 1.0
    h = rng.uniform(0.55, 0.95, n)
    y = (rng.random(n) < expit(np.where(chall, 1.4, 1.0) * logit(h))).astype(np.int64)
    match = np.arange(n) // 10
    rows = Rows(match=match, level=np.where(chall, "chall", "tour"),
                role=np.array(["fit", "fit", "fit", "cal", "test", ""])[match % 6], hold=y,
                prior=h, h=h, Z=Z, opp_dev=np.zeros(n))
    params = fit_params(rows, l2_grid=(10.0,))
    assert params.meta["calibration"]["per_level"]
    assert params.calibration["chall"] != params.calibration["tour"]
    q = np.array([0.6, 0.9])
    steeper = beta_apply(q, params.calibration["chall"])
    assert steeper[0] < q[0] - 0.02 and steeper[1] > q[1] + 0.01


def test_poisoning_the_test_years_leaves_the_fit_unchanged():
    rows = _synthetic_rows(n=30_000, seed=5)
    rng = np.random.default_rng(9)
    out = (rows.role == "test") | (rows.role == "")
    poisoned = Rows(match=rows.match, level=rows.level, role=rows.role,
                    hold=np.where(out, 1 - rows.hold, rows.hold),
                    prior=rows.prior, h=np.where(out, rng.uniform(0.01, 0.99, len(out)), rows.h),
                    Z=np.where(out[:, None], rng.normal(size=rows.Z.shape), rows.Z),
                    opp_dev=rows.opp_dev)
    assert fit_params(poisoned, l2_grid=(0.0, 100.0)) == fit_params(rows, l2_grid=(0.0, 100.0))


# ---- scores ----------------------------------------------------------------------

def test_ece_of_a_known_gap():
    p = np.full(1000, 0.8)
    y = (np.arange(1000) < 700).astype(float)
    assert ece(p, y, np.array([0.0, 1.0])) == pytest.approx(0.1)


def test_ece_adds_gaps_of_either_sign():
    # 0.6 forecast where 0.7 happens, 0.9 where 0.8 happens: the gaps cancel in
    # a sum and in one bin, not in ECE
    p = np.repeat([0.6, 0.9], 1000)
    y = np.concatenate([np.arange(1000) < 700, np.arange(1000) < 800]).astype(float)
    assert ece(p, y, np.array([0.0, 0.75, 1.0])) == pytest.approx(0.1)


def test_the_floor_is_what_a_calibrated_forecast_would_show_not_what_this_one_does():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.75, 0.85, 60_000)
    y = (rng.random(p.size) < p - 0.1).astype(float)      # 0.1 too sure of the hold
    s = calibration_stats(p, y, np.arange(p.size) // 15, n_boot=20)
    assert s["ece"] == pytest.approx(0.1, abs=0.01)
    assert s["ece_floor"] < 0.02


def test_ece_of_calibrated_forecasts_sits_at_its_floor():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.6, 0.95, 60_000)
    y = (rng.random(p.size) < p).astype(float)
    s = calibration_stats(p, y, np.arange(p.size) // 15, n_boot=50)
    assert s["ece"] < 2.5 * s["ece_floor"]
    assert s["cox_slope"] == pytest.approx(1.0, abs=0.1)
    assert s["cox_slope_ci"][0] < 1.0 < s["cox_slope_ci"][1]
    assert len(equal_count_edges(p)) == 16


def test_folds_never_split_a_match():
    match = np.repeat(np.arange(200), np.random.default_rng(0).integers(5, 30, 200))
    fold = match_folds(match, 5, seed=3)
    for m in np.unique(match):
        assert len(set(fold[match == m])) == 1
    assert set(fold) == set(range(5))


def test_choose_l2_deals_its_folds_by_match(monkeypatch):
    calls, real = [], calibrate_mod.match_folds

    def spy(match, folds, seed=0):
        calls.append((match.copy(), folds))
        return real(match, folds, seed)

    monkeypatch.setattr(calibrate_mod, "match_folds", spy)
    rows = _synthetic_rows(n=3_000, seed=8)
    choose_l2(rows.h, rows.Z, rows.hold, rows.match, grid=(0.0, 10.0), folds=5)
    assert len(calls) == 1 and np.array_equal(calls[0][0], rows.match) and calls[0][1] == 5


def _two_levels(shift, n=150_000, seed=0):
    rng = np.random.default_rng(seed)
    q = rng.uniform(0.55, 0.95, n)
    lv = np.array(["tour", "chall"])[rng.integers(0, 2, n)]
    y = (rng.random(n) < expit(logit(q) + np.where(lv == "chall", shift, 0.0))).astype(float)
    return q, y, lv, np.arange(n) // 20


def test_a_map_per_level_must_win_the_fourth_digit():
    # the calibration years of 2026-09-24: per level ahead by 0.00006 -- not enough
    assert not prefer_per_level({"one_map": 0.5216457, "per_level": 0.5215809})
    assert prefer_per_level({"one_map": 0.5217, "per_level": 0.5215})
    assert not prefer_per_level({"one_map": 0.5215, "per_level": 0.5217})


def test_calibration_gets_a_map_per_level_only_when_the_levels_differ():
    per_level, cv = choose_calibration(*_two_levels(0.3))
    assert per_level and cv["per_level"] < cv["one_map"]
    per_level, cv = choose_calibration(*_two_levels(0.0, seed=1))
    assert not per_level and cv["one_map"] <= cv["per_level"]


def test_isotonic_is_monotone_and_pools_violators():
    p = np.array([0.1, 0.2, 0.3, 0.4])
    y = np.array([0, 1, 0, 1])
    fit = isotonic_fit(p, y)
    assert list(fit[1]) == [0.0, 0.5, 1.0]
    got = isotonic_apply(np.array([0.05, 0.25, 0.3, 0.9]), fit)
    assert list(got) == [1e-3, 0.5, 0.5, 1 - 1e-3]


# ---- end to end: the live call is the offline forecast -----------------------------

def test_the_live_function_reproduces_the_evaluated_forecast():
    priced = sim_priced(240, seed=4)
    rows = build_rows(priced)
    params, report = evaluate(rows, n_boot=20, n_boot_coef=0, l2_grid=(100.0,))
    assert set(report["test"]) == {"tour", "chall", "all"}
    final = predict(rows.h, rows.Z, params)
    # the report scores the test years, and its full model is the calibrated one
    test = rows.role == "test"
    for lv in ("tour", "chall", "all"):
        i = test & ((rows.level == lv) if lv != "all" else True)
        assert report["test"][lv]["games"] == int(i.sum())
        assert report["test"][lv]["logloss"]["final"] == pytest.approx(
            log_loss(final[i], rows.hold[i]).mean(), abs=1e-12)
        # the full model is the variant the calibration years chose
        chosen = "final_per_level" if params.meta["calibration"]["per_level"] else "final_one_map"
        assert report["test"][lv]["logloss"]["final"] == pytest.approx(
            report["test"][lv]["logloss"][chosen], abs=1e-12)
        assert 0 <= report["test"][lv]["outside_support"] <= report["test"][lv]["games"]
    for pm in [pm for pm in priced if role_of(pm.match) == "test"][:2]:
        idx = np.flatnonzero(rows.match == priced.index(pm))
        plays = to_plays(pm.match)
        s = start_state(params, pm.match.level, 1, 2, pm.p1, pm.p2)
        at = 0
        for g in pm.match.games:
            if not g.tiebreak:
                ctx = _honest_context(pm, plays, at)
                assert p_hold(s, g.server, ctx, params) == pytest.approx(final[idx[at]], abs=1e-12)
                at += 1
            for srv, won in g.points:
                s = s.after_point(srv, won)
        assert at == len(idx)


# ---- the set marks check ------------------------------------------------------

def test_set_numbers_follow_the_games_and_compare_counts_disagreements():
    games = [_hold(1 + i % 2) for i in range(12)]
    servers = [1 if (n + 1) // 2 % 2 == 0 else 2 for n in range(7)]
    games.append(Game(1, tuple((srv, int(srv == 1)) for srv in servers), tiebreak=True))
    games.append(_hold(2))
    m = MatchPoints("k", "pbp", "tour", 2013, "", "", "a", "b", tuple(games))
    assert set_numbers(m) == [1] * 13 + [2]
    counts, differ = compare([m], {"k": [1] * 13 + [2]})
    assert counts["same"] == 1 and not differ
    counts, differ = compare([m], {"k": [1] * 12 + [2, 2]})
    assert counts["differ"] == 1 and differ == ["k"]
    assert compare([m], {})[0]["no_marks"] == 1


def test_the_marks_are_read_from_both_sources(tmp_path):
    # a repeated id keeps its first row, as load_pbp does
    (tmp_path / "pbp_matches_x.csv").write_text(
        "pbp_id,pbp\n7,SSSS;RRRR.SSSS\n7,SSSS\n", encoding="utf-8")
    assert pbp_marks(tmp_path) == {"7": [1, 1, 2]}
    # markers (server 0) and the women's draw (match number 2xxx) are not games
    rows = ["match_id,SetNo,GameNo,PointServer,PointWinner",
            "2013-x-1101,1,1,1,1", "2013-x-1101,1,1,1,1", "2013-x-1101,1,2,2,1",
            "2013-x-1101,2,3,1,2", "2013-x-1101,0,0,0,0", "2013-x-2101,1,1,1,1"]
    (tmp_path / "2013-x-points.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert slam_marks(tmp_path) == {"2013-x-1101": [1, 1, 2]}
