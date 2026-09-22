# `tennis.ratings` — Elo and as-of serve priors, with state on disk

Track B, step 2. The prior for a match — each player's probability of winning a
point on serve — built the way experiment B1 built it, configured from the B1c
sweep, and packaged so the live process **loads** it instead of replaying
270 thousand matches.

```bash
python -m tennis.ratings download data/sackmann/atp                 # 54 files, 52 MB, once
python -m tennis.ratings build data/sackmann/atp --out data/ratings/atp.json.gz
python -m tennis.ratings prior data/ratings/atp.json.gz "Jannik Sinner" "Carlos Alcaraz" \
    --surface Hard --best-of 5 --as-of 2026-09-22
python -m tennis.ratings evaluate data/sackmann/atp [--config research-b1]
```

`build` takes 10 s and writes 620 KB; loading takes 0.12 s. Both paths live under
`data/`, which git ignores: the files are CC BY-NC-SA 4.0 and never committed.

```python
from tennis.ratings import RatingsSnapshot
snap = RatingsSnapshot.load("data/ratings/atp.json.gz")
pr = snap.prior(206173, 207989, "Hard", best_of=5, as_of="2026-09-22")
pr.p_serve_a, pr.p_serve_b          # the prior; pr also carries every ingredient
snap.add_result(date=..., winner=..., loser=..., surface=..., level=..., w_stats=..., l_stats=...)
```

## What the prior is

For player X against Y, as of the start date of their tournament:

| Piece | What |
|---|---|
| `p_elo` | surface-blended Elo win probability, inverted through the Markov match model (`tennis.markov.invert`) into X's serve-point probability; the pair averages the surface baseline |
| `p_bc` | Barnett–Clarke: baseline + (X's serve rate − tour mean) − (Y's return rate − (1 − tour mean)), rates over the last 366 days shrunk by 200 points toward the tour mean |
| baseline | the surface's serve-points-won rate in the previous year with data — never the current one, which contains the match |
| prior | `0.6 · p_elo + 0.4 · p_bc` |

Configuration, `SWEEP_BEST`: K = 400 / (n + 5)^0.4, surface weight 0.3, 0.6 on Elo.
`RESEARCH_B1` (K = 250, surface weight 0.5, 0.55 on Elo) is kept only to reproduce
the B1 table.

## Equivalence with `research/elo_prior.py`

Measured 2026-09-22 on the real files (mirror `Aneeshers/tennis-sackmann-archive`,
270,662 matches, last tournament 2026-06-01). The research scripts were re-run on the
same download first: `elo_prior_results.json` and `elo_sweep_results.json` came out
byte-identical to the committed ones, so the mirror is the data the numbers were
measured on.

| What | Result |
|---|---|
| Loading and order | identical, all 270,662 rows: keys, ids, dates, surfaces, stats |
| Elo, both configs | **bit-identical**: every pre-match value and the final state of 8,226 players |
| Serve windows | identical except **652 of 411,124** (0.16 %) — see below |
| B1 table under `research-b1` | every RMSE equal to four decimals; largest difference 3·10⁻⁷ |
| Test rows | identical: 121,528 (96,849 Challenger/qualifying, 24,679 tour) |

`tests/test_equivalence.py` pins all of it on synthetic files in Sackmann's format,
bit for bit and row for row; it skips when `research/` or pandas is absent.

**The 652 windows.** A player with two events starting on the same date — in practice
a Slam or Masters qualifying, which Sackmann dates with the main draw although it was
played the week before, and the event he went on to. The overlap rule walks back from
the player's latest earlier event past those still running, and stops at the first that
is over; with two on one date, which one it meets first decides the window. The
original sorted with an unstable quicksort, so that order was an accident. Here the
shorter event goes last, which keeps the qualifying (it was over). Of three
deterministic orders it is also the closest to the original: 652 against 974 and 985.

**The walk-back is right, and subtler than its docstring.** The docstring says
events still running on the date are dropped. The code keeps a running event if a
later one is already over — correctly: a player is in one event at a time, so a
first-round loser at a Slam who plays a Challenger in its second week has his Slam
match behind him. Dropping every running event was tried first and lost that match
in 1,532 windows.

**18 rows fewer than the original** in the B1 evaluation (406,902 against 406,920),
all in training years. The files hold four matches whose winner and loser carry the
same id (data errors); the original's two joins on
(player, match) turned each of the three with stats into 8 rows instead of 2. Here
they stay 2. The Elo still runs through all four exactly as the original did, so
ratings stay bit-identical.

## Numbers under the production config

`python -m tennis.ratings evaluate data/sackmann/atp`, train ≤ 2021, test 2022+:

| Prior | RMSE test | Challenger/quals | tour |
|---|---|---|---|
| Barnett–Clarke | 0.0890 | 0.0909 | 0.0808 |
| Elo inversion | **0.0866** | 0.0877 | 0.0820 |
| Blend, 0.60 on Elo (refitted on train: 0.60) | **0.0850** | **0.0866** | **0.0783** |

Against B1's config the blend gains 0.0005 (0.0855 → 0.0850), almost all of it on
Challenger/qualifying. After subtracting binomial noise as in B1 (0.0554 tour,
0.0591 Challenger), the true error of `p` is 0.0553 on tour and 0.0633 on Challenger
(B1: 0.0554 and 0.0641).

**These differ from the B1c sweep table**, which printed 0.0865 and 0.0849 for this
config and 0.0888 for Barnett–Clarke. `research/elo_sweep.py` computed its baseline
from the *same* year, which contains the match; `elo_prior.py` had been fixed to the
previous year. Same data, same code otherwise: the sweep was 0.0001–0.0002 optimistic.
The choice of config and the 0.60 weight survive the correction.

**Elo as a match forecaster got worse, not better.** K = 400 raises accuracy on the
2022+ matches (0.6452 against 0.6428) but raises log loss too (0.6358 against 0.6307):
the sweep chose by serve RMSE through the inversion, and higher accuracy with higher
log loss is the signature of a win probability that is too confident -- most likely the
larger K. For a serve prior that is the right criterion; anyone using
`win_prob_a` as a match forecast should know it is not calibrated for that.

## Leakage

* `build(as_of=D)` reads only tournaments that started strictly before D.
  `tests/test_snapshot.py` rewrites every later result — winners swapped, stats
  randomised, surfaces changed — and demands the same snapshot, byte for byte.
* `prior(as_of=D)` refuses D before the snapshot's last tournament. The same date is
  allowed, because live the finished rounds of the current tournament are added as
  they end; so a snapshot must never contain the match being priced.
* The snapshot takes the tour mean over its own inputs. The batch path
  (`historical_priors`, `evaluate`) takes it over the whole input, as the original did
  — test years included — to stay comparable with B1. Its effect is a single constant
  near 0.620.

## Limits that matter now

* **The data ends at the tournaments of 2026-06-01.** A prior for a match today is
  priced from ratings 113 days old: every result since is missing from both the Elo
  and the windows, and a player who appeared since gets the initial rating and an
  empty window. `prior` on the command line warns past 30 days. Keeping ratings
  current needs a results source after June 2026; `add_result` is ready for one, and
  none is chosen yet.
* Men only: ATP tour and qualifying/Challenger files. No WTA rating (open in HANDOFF).
* Surfaces are Sackmann's: Hard, Clay, Grass, Carpet. Anything else is refused rather
  than silently priced at the initial rating.
* Names are Sackmann's spelling. Mapping a bookmaker's names to ids is
  `tennis.ingest.names`' job, not done here.

## Files

| File | What |
|---|---|
| `config.py` | `RatingsConfig`, `SWEEP_BEST` (default), `RESEARCH_B1` |
| `sackmann.py` | reading the match files exactly as B1 did, without pandas; `download`, `fingerprint` |
| `elo.py` | `EloState` (update, JSON round trip) and `build_elo` |
| `serve.py` | as-of serve/return windows: `ServeHistory` for the live path, `rolling_windows` for the batch |
| `prior.py` | baseline table, Barnett–Clarke, `Prior`, `historical_priors` (B1's P table) |
| `snapshot.py` | `RatingsSnapshot`: build, save/load, `prior`, `add_result` |
| `evaluate.py` | the B1 RMSE table for any config |
| `__main__.py` | the command line above |
