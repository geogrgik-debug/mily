"""
Elo-anchored serve prior (experiment B1).

Builds surface-blended Elo from Sackmann ATP match files (tour + qualifying/Challenger),
then inverts each match's Elo win probability into a pair of serve-point-win probabilities
(p_serve_A, p_serve_B) through the hierarchical Markov match model, constrained so the pair
averages the surface/year baseline. This is the Klaassen & Magnus (2003) inversion used by
Kovalchik & Reid (2019) and Gollub (2021).

Validated the way Gollub validates: RMSE of the predicted serve-points-won fraction against
what the player actually did in that match, out of time.

Usage: python elo_prior.py <atp_dir> [out_json] [prior_csv]
"""
import sys, os, json, math, glob, warnings
from functools import lru_cache
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

# ------------------------------------------------------------------ Markov match model
@lru_cache(maxsize=None)
def p_game(p):
    """P(server holds from 0-0) under iid point win prob p."""
    if p <= 0.0: return 0.0
    if p >= 1.0: return 1.0
    q = 1.0 - p
    return p**4 * (1 + 4*q + 10*q*q) + 20 * p**3 * q**3 * (p*p / (p*p + q*q))

@lru_cache(maxsize=None)
def p_tiebreak(pa, pb):
    """P(A wins a 7-point tiebreak), A serving the first point.

    From any tie at 6-6 or beyond the next two points are served one by each player,
    so the win-by-two race has the closed form u/(u+v) with u = pa(1-pb), v = (1-pa)pb.
    """
    u = pa * (1.0 - pb)          # A takes both points of the next two-point block
    v = (1.0 - pa) * pb          # B takes both
    tie = 0.5 if (u + v) <= 0 else u / (u + v)
    memo = {}
    def f(a, b, srv):                      # srv 0 = A to serve this point
        if a >= 7 and a - b >= 2: return 1.0
        if b >= 7 and b - a >= 2: return 0.0
        if a >= 6 and b >= 6 and a == b: return tie
        key = (a, b, srv)
        if key in memo: return memo[key]
        p = pa if srv == 0 else 1.0 - pb   # prob A wins this point
        n = a + b + 1                      # index of the point about to be played (1-based)
        nxt = 0 if (n % 4) in (0, 3) else 1
        v_ = p * f(a+1, b, nxt) + (1-p) * f(a, b+1, nxt)
        memo[key] = v_
        return v_
    return f(0, 0, 0)

@lru_cache(maxsize=None)
def p_set(pa, pb):
    """P(A wins a set), A serving the first game, tiebreak at 6-6."""
    ga, gb = p_game(pa), p_game(pb)
    memo = {}
    def f(a, b, srv):
        if a == 6 and b <= 4: return 1.0
        if b == 6 and a <= 4: return 0.0
        if a == 7: return 1.0
        if b == 7: return 0.0
        if a == 6 and b == 6: return p_tiebreak(pa, pb)
        key = (a, b, srv)
        if key in memo: return memo[key]
        w = ga if srv == 0 else 1.0 - gb
        v = w * f(a+1, b, 1-srv) + (1-w) * f(a, b+1, 1-srv)
        memo[key] = v
        return v
    return f(0, 0, 0)

def p_match(pa, pb, best_of=3):
    """P(A wins the match); sets treated as iid, serve order averaged over who starts."""
    s = 0.5 * (p_set(pa, pb) + (1.0 - p_set(pb, pa)))
    need = 2 if best_of == 3 else 3
    return sum(math.comb(need + k - 1, k) * s**need * (1-s)**k for k in range(need))

@lru_cache(maxsize=None)
def _invert(win_prob_r, baseline_r, best_of):
    """Cached bisection: (pa, pb) with mean == baseline reproducing win_prob."""
    win_prob = min(max(win_prob_r, 1e-3), 1 - 1e-3)
    baseline = baseline_r
    lo, hi = 0.0, min(baseline, 1 - baseline) - 1e-3
    if p_match(baseline + hi, baseline - hi, best_of) < win_prob:
        return baseline + hi, baseline - hi
    for _ in range(28):
        d = 0.5 * (lo + hi)
        if p_match(baseline + d, baseline - d, best_of) < win_prob: lo = d
        else: hi = d
    d = 0.5 * (lo + hi)
    return baseline + d, baseline - d

def invert(win_prob, baseline, best_of=3):
    """Round the inputs so the bisection cache does the heavy lifting."""
    return _invert(round(float(win_prob), 3), round(float(baseline), 3), int(best_of))

# ------------------------------------------------------------------ data
def load_matches(atp_dir):
    frames = []
    for f in sorted(glob.glob(os.path.join(atp_dir, "atp_matches_*.csv"))):
        if "futures" in f: continue
        try: d = pd.read_csv(f, low_memory=False)
        except Exception: continue
        d["src"] = "chall_qual" if "qual_chall" in f else "tour"
        frames.append(d)
    m = pd.concat(frames, ignore_index=True)
    m["date"] = pd.to_datetime(m["tourney_date"], format="%Y%m%d", errors="coerce")
    m = m.dropna(subset=["date", "winner_id", "loser_id"])
    m["surface"] = m["surface"].replace("", np.nan).fillna("Hard")
    m["best_of"] = pd.to_numeric(m["best_of"], errors="coerce").fillna(3).astype(int)
    for c in ["w_svpt","w_1stWon","w_2ndWon","l_svpt","l_1stWon","l_2ndWon","w_SvGms","l_SvGms","match_num"]:
        m[c] = pd.to_numeric(m.get(c), errors="coerce")
    m["w_spw"] = (m["w_1stWon"] + m["w_2ndWon"]) / m["w_svpt"]
    m["l_spw"] = (m["l_1stWon"] + m["l_2ndWon"]) / m["l_svpt"]
    m["year"] = m["date"].dt.year
    m = m.sort_values(["date", "match_num"]).reset_index(drop=True)
    m["match_key"] = (m["tourney_id"].astype(str) + "#" + m.index.astype(str))
    return m

# ------------------------------------------------------------------ Elo
def build_elo(m, K0=250.0, Koff=5.0, Kexp=0.4, surface_weight=0.5):
    overall, surf, cnt = {}, {}, {}
    out = np.empty((len(m), 5))
    wid = m["winner_id"].to_numpy(); lid = m["loser_id"].to_numpy(); sf = m["surface"].to_numpy()
    for i in range(len(m)):
        w, l, s = wid[i], lid[i], sf[i]
        ew, el = overall.get(w, 1500.0), overall.get(l, 1500.0)
        sw, sl = surf.get((w, s), 1500.0), surf.get((l, s), 1500.0)
        bw = (1 - surface_weight) * ew + surface_weight * sw
        bl = (1 - surface_weight) * el + surface_weight * sl
        exp_w = 1.0 / (1 + 10 ** ((bl - bw) / 400.0))
        nw, nl = cnt.get(w, 0), cnt.get(l, 0)
        out[i] = (bw, bl, exp_w, nw, nl)
        kw = K0 / (nw + Koff) ** Kexp; kl = K0 / (nl + Koff) ** Kexp
        overall[w] = ew + kw * (1 - exp_w); overall[l] = el - kl * (1 - exp_w)
        surf[(w, s)] = sw + kw * (1 - exp_w); surf[(l, s)] = sl - kl * (1 - exp_w)
        cnt[w] = nw + 1; cnt[l] = nl + 1
    for j, c in enumerate(["elo_blend_w", "elo_blend_l", "elo_exp_w", "n_matches_w", "n_matches_l"]):
        m[c] = out[:, j]
    return m, overall, surf, cnt

# ------------------------------------------------------------------ rolling serve/return priors
def rolling_priors(m, months=12, k_shrink=200.0, strict_overlap=True):
    """Long player-match table with as-of rolling serve/return rates.

    The window covers matches whose tournament started strictly before this one, so no match
    feeds its own prior. Sackmann stamps every match of a tournament with the tournament start
    date, which leaves within-tournament order ambiguous; excluding the whole tournament is the
    only clean as-of definition, and it also keeps the (player, match) join key unique.

    strict_overlap additionally drops matches from events that had started earlier but were
    still being played on this match's start date: a 14-day Slam overlaps the Challengers that
    start during its second week, so its late rounds would otherwise land in their priors.
    Sackmann publishes no end date, so the span is inferred from the level and draw size.
    """
    span = np.where(m["tourney_level"].isin(["G"]), 14,
            np.where(m["tourney_level"].isin(["M", "F", "D"]), 12, 7))
    m = m.assign(t_end=m["date"] + pd.to_timedelta(span, unit="D"))
    cols = ["date","t_end","match_key","pid","oid","surface","best_of","svpt","w1","w2","o_svpt","o_w1","o_w2"]
    a = m[["date","t_end","match_key","winner_id","loser_id","surface","best_of",
           "w_svpt","w_1stWon","w_2ndWon","l_svpt","l_1stWon","l_2ndWon"]].copy()
    a.columns = cols
    b = m[["date","t_end","match_key","loser_id","winner_id","surface","best_of",
           "l_svpt","l_1stWon","l_2ndWon","w_svpt","w_1stWon","w_2ndWon"]].copy()
    b.columns = cols
    a["won"] = 1; b["won"] = 0
    long = pd.concat([a, b], ignore_index=True)
    long["spw_n"] = long["svpt"]; long["spw_k"] = long["w1"] + long["w2"]
    long["rpw_n"] = long["o_svpt"]; long["rpw_k"] = long["o_svpt"] - (long["o_w1"] + long["o_w2"])
    long = long.dropna(subset=["spw_n","spw_k","rpw_n","rpw_k"])
    long = long[(long["spw_n"] > 0) & (long["rpw_n"] > 0)].sort_values("date").reset_index(drop=True)
    tour_spw = long["spw_k"].sum() / long["spw_n"].sum()

    recs = []
    win = np.timedelta64(int(months * 30.5), "D")
    for pid, d in long.groupby("pid", sort=False):
        d = d.sort_values("date")
        dates = d["date"].to_numpy(); keys = d["match_key"].to_numpy()
        ends = d["t_end"].to_numpy()
        cs = {c: np.concatenate([[0.0], d[c].to_numpy().cumsum()]) for c in ["spw_k","spw_n","rpw_k","rpw_n"]}
        lo = np.searchsorted(dates, dates - win, side="left")   # window start
        hi = np.searchsorted(dates, dates, side="left")         # strictly earlier tournaments
        for i in range(len(d)):
            j, e = lo[i], hi[i]
            if strict_overlap:
                # walk back past any earlier event that was still running on this date
                while e > j and ends[e-1] > dates[i]:
                    e -= 1
            recs.append((pid, keys[i],
                         cs["spw_k"][e]-cs["spw_k"][j], cs["spw_n"][e]-cs["spw_n"][j],
                         cs["rpw_k"][e]-cs["rpw_k"][j], cs["rpw_n"][e]-cs["rpw_n"][j], int(e)))
    R = pd.DataFrame(recs, columns=["pid","match_key","spw_k_w","spw_n_w","rpw_k_w","rpw_n_w","n_prev_matches"])
    R["spw_raw"] = (R["spw_k_w"] + k_shrink * tour_spw) / (R["spw_n_w"] + k_shrink)
    R["rpw_raw"] = (R["rpw_k_w"] + k_shrink * (1 - tour_spw)) / (R["rpw_n_w"] + k_shrink)
    return long, R, tour_spw

# ------------------------------------------------------------------ evaluation
def evaluate(atp_dir, out_json="elo_prior_results.json", prior_csv=None):
    print("loading...", flush=True)
    m = load_matches(atp_dir)
    print(f"matches: {len(m)}  {m['date'].min().date()}..{m['date'].max().date()}", flush=True)
    m, overall, surf, cnt = build_elo(m)
    long, R, tour_spw = rolling_priors(m)
    print(f"tour SPW baseline = {tour_spw:.4f}", flush=True)

    # surface/year baseline for the inversion constraint
    bl = long.groupby([long["date"].dt.year, "surface"]).apply(
        lambda d: d["spw_k"].sum() / d["spw_n"].sum()).rename("baseline").reset_index()
    bl.columns = ["year", "surface", "baseline"]

    # one row per player-match with Elo-implied win prob
    cols = ["date","year","surface","best_of","match_key","pid","oid","pname","oname",
            "win_prob","n_self","n_opp","svpt","w1","w2","src"]
    a = m[["date","year","surface","best_of","match_key","winner_id","loser_id","winner_name","loser_name",
           "elo_exp_w","n_matches_w","n_matches_l","w_svpt","w_1stWon","w_2ndWon","src"]].copy()
    a.columns = cols
    b = m[["date","year","surface","best_of","match_key","loser_id","winner_id","loser_name","winner_name",
           "elo_exp_w","n_matches_l","n_matches_w","l_svpt","l_1stWon","l_2ndWon","src"]].copy()
    b.columns = cols
    b["win_prob"] = 1.0 - b["win_prob"]
    P = pd.concat([a, b], ignore_index=True)
    P["spw_actual"] = (P["w1"] + P["w2"]) / P["svpt"]
    P = P.dropna(subset=["spw_actual", "svpt"])
    P = P[P["svpt"] >= 30]
    P = P.merge(bl, on=["year","surface"], how="left")
    P["baseline"] = P["baseline"].fillna(tour_spw)
    P = P.merge(R[["pid","match_key","spw_raw","rpw_raw","spw_n_w","n_prev_matches"]],
                on=["pid","match_key"], how="left")
    P = P.merge(R[["pid","match_key","spw_raw","rpw_raw"]].rename(
        columns={"pid":"oid","spw_raw":"opp_spw_raw","rpw_raw":"opp_rpw_raw"}),
                on=["oid","match_key"], how="left")
    P = P.dropna(subset=["spw_raw","opp_rpw_raw"])

    print(f"player-match rows: {len(P)}", flush=True)
    # priors
    print("inverting Elo -> p_serve ...", flush=True)
    P["p_elo"] = [invert(w, bs, bo)[0] for w, bs, bo in zip(P["win_prob"], P["baseline"], P["best_of"])]
    # Barnett-Clarke: f_ij = f_t + (f_i - f_av) - (g_j - g_av); g = opponent return rate
    P["p_bc"] = P["baseline"] + (P["spw_raw"] - tour_spw) - (P["opp_rpw_raw"] - (1 - tour_spw))
    P["p_raw"] = P["spw_raw"]
    P["p_const"] = P["baseline"]
    # blend of Elo and BC, weight fitted on train
    train = P[P["year"] <= 2021]; test = P[P["year"] >= 2022]
    best_w, best_r = 0.0, 9e9
    for w in np.linspace(0, 1, 21):
        r = np.sqrt((((w*train["p_elo"] + (1-w)*train["p_bc"]) - train["spw_actual"])**2).mean())
        if r < best_r: best_r, best_w = r, w
    for d in (P, train, test):
        d["p_blend"] = best_w * d["p_elo"] + (1 - best_w) * d["p_bc"]

    def rmse(d, col): return float(np.sqrt(((d[col] - d["spw_actual"])**2).mean()))
    res = {"n_matches": int(len(m)), "n_player_matches": int(len(P)), "tour_spw": float(tour_spw),
           "blend_weight_elo": float(best_w), "rmse": {}, "rmse_by_year": {}, "rmse_by_src": {}}
    for col in ["p_const","p_raw","p_bc","p_elo","p_blend"]:
        res["rmse"][col] = {"train": rmse(train, col), "test_2022plus": rmse(test, col), "all": rmse(P, col)}
        print(f"  {col:9s} train={rmse(train,col):.4f}  test={rmse(test,col):.4f}", flush=True)
    for y, d in test.groupby("year"):
        res["rmse_by_year"][int(y)] = {c: rmse(d, c) for c in ["p_raw","p_bc","p_elo","p_blend"]}
    for s, d in test.groupby("src"):
        res["rmse_by_src"][str(s)] = {"n": int(len(d)), **{c: rmse(d, c) for c in ["p_raw","p_bc","p_elo","p_blend"]}}
    # match-level win prediction sanity
    mm = m[m["year"] >= 2022]
    res["elo_match_accuracy_test"] = float((mm["elo_exp_w"] > 0.5).mean())
    res["elo_match_logloss_test"] = float(-np.log(np.clip(mm["elo_exp_w"], 1e-6, 1)).mean())
    print(f"  Elo match accuracy (2022+) = {res['elo_match_accuracy_test']:.4f}, "
          f"log loss = {res['elo_match_logloss_test']:.4f}", flush=True)

    with open(out_json, "w") as f: json.dump(res, f, indent=1)
    if prior_csv:
        keep = P[["date","year","match_key","pid","oid","pname","oname","surface","best_of","win_prob",
                  "p_elo","p_bc","p_blend","p_raw","baseline","spw_n_w","n_prev_matches","n_self",
                  "spw_actual","svpt","src"]]
        keep.to_csv(prior_csv, index=False)
        print("wrote", prior_csv, len(keep), flush=True)
    return res

if __name__ == "__main__":
    evaluate(sys.argv[1] if len(sys.argv) > 1 else "atp",
             sys.argv[2] if len(sys.argv) > 2 else "elo_prior_results.json",
             sys.argv[3] if len(sys.argv) > 3 else None)
