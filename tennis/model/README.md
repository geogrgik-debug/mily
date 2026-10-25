# `tennis.model`: rows for the model, and the calibrated P(hold)

Track B, steps 3 and 5.

- `game_rows.py` builds one leak-safe row per service game, from the games
  before it only. Its docstring and tests cover how.
- `hold.py` computes P(server holds the next game): the live state
  (`tennis.state`), plus a residual in the game's context
  (`tennis.features`), plus a beta calibration. The audit calls this the
  hybrid, version 1 (section 11.2).
- `hold_v1.json` holds the fitted parameters. `python -m tennis.eval calibrate`
  writes it.

## Live use

```python
from tennis.features import GameContext
from tennis.model import HoldParams, p_hold, prior_for_match, start_state

params = HoldParams.load()                                   # tennis/model/hold_v1.json
pf = prior_for_match(snap, a, b, "Clay", 3, as_of="2026-09-21")   # one prior per match
s = start_state(params, "chall", a, b, pf(a, b), pf(b, a))   # n0 the parameters were fitted with
s = s.after_point(server=a, server_won=1)                    # ...every point, tie-breaks too
ctx = GameContext(level="chall", surface="Clay", best_of=3, set_no=1,
                  server_games=3, returner_games=2, server_sets=0, returner_sets=0,
                  served_before=3, just_broke=False, was_broken=False)
p_hold(s, b, ctx, params)                                    # b serves next: calibrated P(hold)
```

`prior_for_match` calls `RatingsSnapshot.prior` once per match and reads both
serves off that one call, so the two players' priors cannot come from
different dates or surfaces. `start_state` opens the match with the n0 in the
parameters (Slams 140, tour 100, Challenger 90). A state started with another
n0 would feed the residual an h it was not fitted on.

The live function and the offline measurement go through the same `predict`.
`tennis/eval/tests/test_calibrate.py` plays a match point by point through
`p_hold` and checks the forecasts equal the evaluated ones to 1e-12.

## The model

    h       = MatchState.p_hold_next(server)          live state: Beta per server, Markov game averaged over it
    logit q = logit(h) + c + beta . z                 z: 15 context features, tennis.features.FEATURES
    logit P = a ln q - b ln(1 - q) + d                beta calibration

- The live state enters as an offset with its coefficient fixed at one.
- `c` and `beta` come from one logistic regression for Slams, tour and
  Challenger. The level is two of its features. The L2 penalty is chosen by
  5-fold cross-validation by match; it chose 10, and anything from 0 to 100
  gives the same held-out log loss within 2e-6.
- The beta map is fitted afterwards, on later years the residual never saw.
  Over the narrow range of hold forecasts a and b are poorly pinned one by one,
  while the map they make is well pinned. If one comes out negative it is
  fixed at zero, as Kull et al. do. Isotonic, fitted on the same rows, scores
  the same on every level (the gains below), so the smooth map is kept.
- One map serves all three levels. A map per level must beat it by 0.0001 of
  held-out log loss, by 5-fold cross-validation by match inside the
  calibration years. It won by 0.00006, and the test years show why that is
  not enough (below). The rule's threshold was set on 2026-09-24, after the
  test had been seen. `hold_v1.json` still carries a map per level, three
  copies of one map, so the live code already looks the map up by level.
- **Beyond the data the map does not extrapolate.** Its support is the
  calibration years' q without the 0.1 % at each end: 0.472 to 0.951. Past
  either edge the map's correction to the logit stays at its value at that
  edge. The forecast still rises with q, but the map's slope there, fitted on
  nothing, is dropped. At q = 0.3 the map alone would add 5.8 points, and held
  it adds 1.4. At q = 0.2 it would add 8.4, and held it adds 1.1. Of the test
  games, 343 of 218 848 (0.16 %) fall outside the support, and log loss does
  not move in the fourth digit.

The years, the data and how rows are built are in `tennis/eval/README.md`,
section "Calibration". In short: everything fitted is from before 2017, and
everything scored is from 2017 on, on years no fit has seen.

## What it buys

The command is `python -m tennis.eval calibrate --json data/eval/calibrate.json`,
about 14 minutes. It covers every regular service game of the test years.
Intervals are 95 %, from resampling whole matches.

| log loss | Slams 2019–2024 | tour 2017 | Challenger 2017 | all |
|---|---|---|---|---|
| games | 68 773 | 52 012 | 98 063 | 218 848 |
| prior alone | 0.4955 | 0.5134 | 0.5504 | 0.5244 |
| live state | 0.4922 | 0.5095 | 0.5442 | 0.5196 |
| + context residual | 0.4907 | 0.5086 | 0.5428 | 0.5183 |
| + beta calibration | 0.4903 | 0.5088 | 0.5430 | 0.5183 |

| gain | Slams | tour | Challenger | all |
|---|---|---|---|---|
| prior → live state | +0.0034 (0.0027–0.0041) | +0.0039 (0.0030–0.0048) | +0.0062 (0.0055–0.0070) | +0.0048 (0.0043–0.0052) |
| live → + residual | +0.0014 (0.0010–0.0018) | +0.0008 (0.0004–0.0013) | +0.0014 (0.0010–0.0017) | +0.0013 (0.0010–0.0015) |
| + residual → + calibration | +0.0004 (0.0003–0.0006) | −0.0002 (−0.0003–−0.0000) | −0.0001 (−0.0002–−0.0000) | +0.0000 (−0.0000–+0.0001) |
| live + calibration alone → the full model | +0.0013 (0.0009–0.0017) | +0.0009 (0.0005–0.0014) | +0.0012 (0.0009–0.0015) | +0.0012 (0.0010–0.0014) |

The last row is the context's own worth. A calibration of the live state alone
cannot reach it.

The tour's prior → live gain here is +0.0039 against +0.0043 in
`tennis/state/README.md`. The population differs: every service game here
against games after the server's first there, and tour without Slam matches
here, which tennis_pointbypoint also carries.

**Calibration.** ECE is on 15 equal-count bins. The floor is the ECE a
perfectly calibrated forecast with these same probabilities would show from
noise alone. Cox fits logit P(hold) = intercept + slope · logit p, and a
calibrated forecast has (0, 1). The audit's bar is ECE < 0.02 and slope in
[0.9, 1.1] (section 17.2).

| | ECE [floor] | Cox intercept | Cox slope |
|---|---|---|---|
| Slams, live state | 0.0162 [0.0046] | −0.293 (−0.358 – −0.229) | 1.154 (1.109–1.200) |
| Slams, + residual | 0.0142 [0.0046] | −0.187 (−0.244 – −0.132) | 1.078 (1.038–1.119) |
| Slams, full model | **0.0090** [0.0047] | −0.108 (−0.163 – −0.055) | **1.043** (1.005–1.084) |
| tour, live state | 0.0066 [0.0055] | +0.002 (−0.061 – +0.066) | 1.019 (0.970–1.072) |
| tour, + residual | 0.0067 [0.0055] | +0.057 (−0.005 – +0.116) | 0.965 (0.918–1.013) |
| tour, full model | **0.0097** [0.0055] | +0.111 (+0.051 – +0.168) | **0.948** (0.900–0.997) |
| Challenger, live state | 0.0092 [0.0043] | −0.161 (−0.199 – −0.118) | 1.146 (1.109–1.182) |
| Challenger, + residual | 0.0065 [0.0041] | −0.065 (−0.100 – −0.026) | 1.065 (1.031–1.097) |
| Challenger, full model | **0.0089** [0.0042] | −0.034 (−0.069 – +0.004) | **1.073** (1.038–1.105) |
| all, live state | 0.0058 [0.0026] | −0.121 (−0.151 – −0.092) | 1.085 (1.062–1.108) |
| all, full model | **0.0039** [0.0027] | +0.006 (−0.021 – +0.033) | **1.009** (0.987–1.030) |

- The live state alone is under-confident on Slams and Challenger: slope 1.15,
  outside the audit's bar. The residual brings the slope inside, and the full
  model passes on every level.
- Pooled, the full model is calibrated: slope 1.009, intercept +0.006, ECE
  within 0.0012 of its floor.
- **One calibration map serves three levels, and it trades them.** It helps
  the Slams (log loss +0.0004, ECE 0.0142 → 0.0090). It slightly hurts tour
  (−0.0002, ECE 0.0067 → 0.0097, intercept pushed to +0.11) and Challenger
  (−0.0001).
- **A map per level does not do better.** Fitted on the same calibration years
  and scored on the test:

  | | one map | a map per level | gain of a map per level |
  |---|---|---|---|
  | Slams | ECE 0.0090, slope 1.043 | ECE 0.0058, slope 0.992 | +0.0003 (0.0001–0.0004) |
  | tour | ECE 0.0097, slope 0.948 | ECE 0.0107, slope 0.903 | −0.0002 (−0.0003–−0.0001) |
  | Challenger | ECE 0.0089, slope 1.073 | ECE 0.0095, slope **1.116** | −0.0001 (−0.0001–−0.0000) |
  | all | ECE 0.0039, slope 1.009 | ECE 0.0043, slope 1.011 | +0.0000 (−0.0001–+0.0001) |

  It helps the Slams and hurts tour and Challenger. On Challenger it takes the
  slope outside the audit's bar. Tour and Challenger are calibrated on one
  year, 2015, and tested on 2017, so a map of their own learns 2015. One map
  stays; the owner chose it on 2026-09-24. A calibration fitted on recent
  games, the live recordings once there are enough of them, is the real fix.
- The ECE intervals come from resampling, which adds noise, so they sit above
  the point estimate. Read them against the floor, not against zero.

## What the context says

Coefficients are in the logit, fitted on the fit years, with 95 % intervals
from 200 refits by match. Scale: 0.1 in the logit near P = 0.8 is about 1.6
points of P(hold).

| feature | coef | | feature | coef |
|---|---|---|---|---|
| intercept | +0.022 (+0.001 – +0.044) | | serving for the set | +0.107 (+0.078 – +0.140) |
| Slam | +0.023 (−0.011 – +0.058) | | serving to stay in the set | −0.073 (−0.099 – −0.050) |
| Challenger | −0.030 (−0.044 – −0.015) | | ahead in sets | +0.052 (+0.015 – +0.093) |
| clay | +0.077 (+0.063 – +0.091) | | behind in sets | −0.127 (−0.162 – −0.095) |
| grass | +0.013 (−0.021 – +0.045) | | deciding set | −0.065 (−0.128 – −0.007) |
| first service game | +0.027 (+0.000 – +0.065) | | broke in the game just before | +0.129 (+0.112 – +0.146) |
| his service games so far, /10 | −0.265 (−0.348 – −0.182) | | his own last service game broken | −0.083 (−0.098 – −0.065) |
| set number − 1 | +0.106 (+0.060 – +0.155) | | | |
| games in the set, /10 | +0.105 (+0.056 – +0.154) | | | |

Which group carries the gain: the residual refitted without it, test log loss
lost.

| without | Slams | tour | Challenger | all |
|---|---|---|---|---|
| level and surface | +0.0000 (−0.0001–+0.0001) | +0.0004 (0.0003–0.0005) | +0.0000 (−0.0001–+0.0001) | +0.0001 (0.0001–0.0002) |
| the scoreboard | +0.0008 (0.0005–0.0010) | +0.0003 (0.0000–0.0006) | +0.0008 (0.0006–0.0010) | +0.0007 (0.0005–0.0008) |
| the last two games | +0.0002 (0.0000–0.0004) | −0.0000 (−0.0002–+0.0002) | +0.0002 (0.0001–0.0004) | +0.0002 (0.0001–0.0003) |

- The residual's +0.0013 is above the +0.001 at which this project first looks
  for a leak, so it was taken apart. It is spread over the ordinary scoreboard
  (sets, set point, games played), all of it known before the first point.
  The rows are tested against poisoned futures and against a scoreboard
  counted by hand.
- No single feature or level carries it.
- The previous games' outcomes add +0.0002, the same order as experiment B2's
  +0.0001.
- The scoreboard was not in B2, so its +0.0007 is new, not a contradiction.

**The returner's serve**, the linear shadow of the joint state (version 2), is
measured apart and kept out of the parameters. Refitted with it:
−0.204 per 0.1 of his serve deviation, a gain of +0.0002 (0.0001–0.0002) over
the full model, +0.0003 on Challenger, zero on tour.

## Limits

- Tour and Challenger are tested on one year, 2017, and Slams on 2019–2024.
  Nothing here is from 2025–2026, so drift since then is unmeasured.
- Nothing here has met BetBoom's feed. The context comes from the feed's score
  in step 6, the replay.
- Men only, and the three levels share one calibration map (see above). Its
  calibration years are 2015–2016, ten to eleven years before the games it
  will price.
- Rows with the server's first service game are included. There the live
  state is still the prior.
