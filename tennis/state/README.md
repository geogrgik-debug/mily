# `tennis.state` — the live serve state, version 1

Track B, step 4. For each player, a belief about P(he wins a point on his own
serve, today, against this opponent): a Beta centred on the pre-match prior
(`tennis.ratings`) with the weight of `n0` phantom points. Every point he serves
adds one real point. From the belief comes P(he holds), through `tennis.markov`.

```python
from tennis.ratings import RatingsSnapshot
from tennis.state import MatchState, N0

pr = snap.prior(a, b, "Hard", best_of=3, as_of="2026-09-22")
s = MatchState.start(a, b, pr.p_serve_a, pr.p_serve_b, n0=N0["chall"])   # default: N0["tour"]
s = s.after_point(server=a, server_won=1)      # a new state; the old one is untouched
s.p_hold_next(a)          # the next game, not started yet: markov.p_game, averaged
s.p_hold_now(a, 2, 1)     # a game under way at 30-15:      markov.p_hold_from, averaged
s.belief(a).mean, s.belief(a).sd
```

## Two quantities, not four

The audit's state is (S_A, R_A, S_B, R_B), serve and return of each player
(section 9.4). Point outcomes only see two contrasts, A's serve against B's
return and B's serve against A's. A state driven by points therefore tracks
those two, one Beta per server.

What points can say about the other two dimensions is whether the two contrasts
move together. That is measured (`tennis.eval`, "serve link" below). They do, strongly.
When a server beats his prior, his opponent's serve tends to fall short of
his: the correlation of their true deviations is between −0.55 and −0.60 in
every segment. Version 1 ignores this. It is the case for version 2, a joint
state (a Kalman filter on logit p with that covariance). Version 2 waits for
the owner.

## The hold probability is averaged over the belief

Given p, the points of a game are iid. So the probability of a hold is the
Markov game integrated against the posterior, not the game at its mean
(audit, section 9.3). Where servers live the game is concave in p, and the
game at the mean overstates the hold. The integral runs on a 1000-node grid.
`p_hold_plugin` is the game at the mean, kept for comparison. Measured, the
averaging is worth +0.0004 on the Slams (interval 0.0003–0.0005) and zero on
tour and Challenger.

## n0 is fitted, not chosen

`python -m tennis.eval live-state` fits n0 on the training years by the lowest
log loss of the hold forecast. It checks the result two ways: by the likelihood
of every single serve point given the ones before it, and by the audit's
split-half test.

| Segment (training years) | n0, hold log loss | 95 %, by match | n0, point likelihood | split-half, k = 2 games |
|---|---|---|---|---|
| Grand Slam men (2012–2018) | **140** | 120–140 | 111 | 116 |
| tennis_pointbypoint ATP (2011–2015) | **100** | 100–120 | 90 | 101 |
| tennis_pointbypoint Challenger (2011–2015) | **90** | 90–100 | 82 | 84 |

The curve is flat near its minimum: ±10 % costs under 0.0001 of log loss,
±20 % up to 0.0002. The point likelihood puts n0 10–20 % lower, which costs
about 0.00005 on the hold forecast. The two criteria agree for any practical
purpose. The constants live
in `beta.py` as `N0`; `DEFAULT_N0` is the tour's.

**Against the audit's 16–27 %.** With these n0 the share of an early deviation
carried forward is 8–12 % after 12 serve points, 15–21 % after 24 and 21–29 %
after 37. The audit measured 16 %, 24 % and 27 % at those points, with a crude
prior. A better prior leaves less to learn inside the match, so the lower end
at 12 points is what it should be.

The same split-half on our data gives slopes of 0.10–0.13 at 2 games and
0.26–0.44 at 6. The implied n0 falls as k grows (tour 101 → 60, Challenger
84 → 47): later deviations carry more than a constant form with one n0 would
say. Two likely reasons, neither measured yet. One is selection: only a player
with seven or more service games counts at k = 6, and long matches may be
where the prior was more wrong. The other is a prior whose error differs from
player to player; the gain by rated matches below points the same way. The
k = 2 row carries the least selection, and it matches the point likelihood.

**n0 = 40 was too weak.** That is the `SPW_SHRINK_POINTS` in
`tennis/model/game_rows.py` until now. With 40, the live state is worse than
with the fitted n0 by 0.0035 on the Slams (0.0029–0.0040), 0.0034 on tour and
0.0019 on Challenger. On the Slams, n0 = 40 is no better than the prior alone.

## What it buys

Test years never seen by the fit: Slams 2019–2024 (experiment B2's split),
tennis_pointbypoint 2017. Games where the server has already served in the
match, B2's population. Gain = log loss of the prior alone minus log loss of
the live state, averaged the same way. Interval: 95 %, resampling whole
matches.

| Segment | games | gain | after recalibrating both on training years | if fed the opponent's serve instead |
|---|---|---|---|---|
| Slams 2019–2024 | 64 921 | **+0.0036** (0.0029–0.0043) | +0.0037 (0.0028–0.0046) | −0.0117 |
| tour 2017 | 64 378 | **+0.0043** (0.0034–0.0051) | +0.0040 (0.0031–0.0050) | −0.0102 |
| Challenger 2017 | 89 097 | **+0.0068** (0.0061–0.0077) | +0.0067 (0.0059–0.0076) | −0.0147 |

The Slam gain sits next to B2's +0.0030 (0.0023–0.0036), measured on the same
kind of data with a logistic regression. The Challenger gain sits next to the
audit's +0.0062 on the Match Charting Project. Every gain survives
recalibrating both models, so it is information, not a fix to a biased prior.
Feeding the server's belief with his opponent's serve points makes the
forecast worse than the prior alone in every segment, so the sides are right.

The gain is largest where the prior knows least. On Challenger, for servers
with 0–52 rated matches behind their Elo it is +0.0099 (0.0084–0.0115). For
those with 186–1003 it is +0.0046 (0.0032–0.0059). An n0 that shrinks with the
prior's evidence is a candidate for later. On tour and Slams the three bands'
intervals overlap.

## Leaks

The state after point k is a pure fold of points 1..k. `after_point` returns a
new state, so a later point cannot reach an earlier one.
`tests/test_beta.py::test_the_state_after_point_k_ignores_every_later_point`
flips every later point, server and outcome both. It demands every earlier
state and price unchanged. The evaluation's rows are built the same way and
tested the same way (`tennis/eval/tests/test_live_state.py`). One more test
pins those rows equal to `MatchState` folded over the same points.

## Limits

- Form is constant within a match, and one n0 serves every player of a
  level. The split-half slopes rise with k faster than that predicts (see
  above); a drifting form would do the opposite, so the audit's random walk is
  not what these data ask for first.
- The data are old: Slams to 2024, tennis_pointbypoint 2011–2015 and 2017.
  Ratings end at the tournaments of 1 June 2026.
- Men only: there is no WTA rating.
- Sackmann's files carry Challenger qualifying only from 2017, so 4 963
  qualifying matches of tennis_pointbypoint could not be priced; the 2017
  test year includes 1 649 of them, the training years almost none.
