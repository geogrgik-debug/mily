# `tennis.markov` — hierarchical Markov tennis model

Point → game → tiebreak → set → match, plus the inversion that turns a match win
probability back into a pair of serve-point-win probabilities. Pure functions, no
I/O, no global state beyond memo caches.

## What it exposes

| Function | Meaning |
|---|---|
| `p_game(p)` | P(hold) from 0-0, closed form |
| `p_hold_from(p, a, b)` | P(hold) from an arbitrary in-game score — what the live engine calls |
| `exact_score_dist(p)` | the eight-outcome game market as a dict |
| `p_tiebreak(pa, pb, target=7)` | 1-2-2 rotation, `target=10` for a match tiebreak |
| `p_set(pa, pb)` | A serving first, tiebreak at 6-6 |
| `p_match(pa, pb, best_of=3)` | sets iid, opening serve averaged |
| `invert(win_prob, baseline, best_of=3)` | Klaassen & Magnus inversion, pair averages `baseline` |

## Provenance

`p_game`, `p_tiebreak`, `p_set`, `p_match` and `invert` are lifted from
`research/elo_prior.py`, which produced the experiment-B numbers.
`p_hold_from` and `exact_score_dist` come from `research/calc.py`, which cannot be
imported at all because it runs multi-minute simulations at module level — lifting
them was the point of this module.

`tests/test_equivalence.py` pins the first five against the original bit for bit.
If it fails, `docs/EXPERIMENT_B_elo_prior_and_process.md` no longer describes this
code.

## Two things the tests exist to stop coming back

**The underdog bug.** `_invert` bisects over `d >= 0` only, so it solves for a
favourite; `invert` handles the other half by symmetry. Without that swap every
`win_prob < 0.5` returned `(baseline, baseline)` — half of all values junk — and
the original sanity check missed it because it only tried 0.5, 0.6, 0.75 and 0.9.
`test_invert_round_trip` now sweeps 0.05 to 0.95 across three baselines and both
formats, and `test_invert_does_not_collapse_to_the_baseline_for_underdogs` asserts
the specific shape of the failure.

**Recursions agreeing with each other.** A shared logical error in the game, set
and tiebreak recursions would cancel out if they were only cross-checked against
one another, so the closed forms are also checked against a vectorised
point-level Monte Carlo written in a different style (`_mc_game`, `_mc_tiebreak`,
`_mc_set`), at 200 000 trials with a 0.005 tolerance and a fixed seed.

## Modelling assumptions, and why

Points are iid given the server. This is an assumption, not a fact, but it is the
one our own measurements support: on 66 697 men's Grand Slam service games,
previous-game outcomes bought +0.0001 log loss and Hawk-Eye process features
bought −0.0001. There is no measured effect left for a point-level dependence
term to capture.

The opening serve of a set is averaged in `p_match` rather than carried. Who
serves first is an unmodelled coin flip once a match is under way, and carrying it
would make `invert` depend on a variable the prior never observes.

`invert` rounds its inputs to three decimals so the cache hits across a quarter of
a million matches. That costs about 5e-4, against a prior error of 5.5 points.
