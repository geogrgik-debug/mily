# `tennis.market` — reading the bookmaker's board

Results from this module are in
[`docs/MARKET_MARGINS_2026-09-22.md`](../../docs/MARKET_MARGINS_2026-09-22.md).

| Module | What |
|---|---|
| `names.py` | `market_name` → `MarketRef(scope, set_no, game_no, kind)`, and outcome names → structure |
| `overround.py` | Book sum, margin, proportional normalisation, Shin |
| `fit.py` | The single point probability behind an eight-outcome game book |
| `measure.py` | CLI: margins and fit quality from a captured raw log |
| `diff.py` | Which prices moved between two consecutive snapshots of a match |
| `streams.py` | Raw log → quotes → price events (margin-free probability of an outcome, emitted when it changes); refuses logs not written on one machine |
| `lead_lag.py` | CLI: which of two books moves a price first, by how much, and whether the leader is stable |

## The one thing to know before using this

**BetBoom prices the next game, not the one being played** — 74% of quoted
game-winner markets, measured. With the scoreboard at game 9 the markets are for
game 10.

That is exactly what this project models, which is the good news. The trap is
that the *server changes*: reading `scoreboard.serving_side` and applying it to
a next-game market mirrors every probability to its complement, and the result
still looks entirely plausible. `GameContext.server_of()` does the alternation,
and returns `None` past game 12 because a tiebreak uses the 1-2-2 rotation.

A game market also carries its whole address inside `market_name`
(`"2-й сет 6-й гейм: Точный счёт"`) with `period_name` empty, so without
parsing that string there is no way to say which game a price belongs to.

## Why margin removal is a parameter, not a constant

Shin and proportional normalisation disagree by more than a point of hold
probability on these books, and the project's whole target is a 5.5-point prior
error. Something that size cannot be a default buried in a helper.

Measured on 108 games where both the two-way and the eight-way market were
quoted at once, the two implied `P(hold)` values differ by a median of 1.09
points under Shin and 1.38 under proportional — Shin smaller, which is the
direction the favourite-longshot bias predicts. It does not reach zero, so the
bookmaker's own number is itself only defined to about a point.

## The eight outcomes really are one number

Confirmed on 108 live books, median largest residual 0.01. A book quoting eight
exact-score prices is revealing a single point probability and nothing more,
which is why no separate exact-score model is built.

## Who moves first: `lead_lag`

    python -m tennis.market.lead_lag betboom=PATH 1win=PATH [--events moves.csv]

Three numbers per pair of books: **order** (who moved first, per move and in
total), **lag** (median and p90, in seconds), **stability** (share of moves the
leader won with a 95% interval, and whether it also leads match by match). A
price is the outcome's probability with the margin removed, and moves of one
outcome are paired in time order, in the same direction, within 10 s.

Two rules come from measurement, not taste:

* **One machine.** Order between logs is only comparable on one clock, so the
  logs are checked against each other (wall minus monotonic time, at the same
  moments) and refused if they come from two machines. Lags are taken on the
  monotonic clock: on the laptop the wall clock jumped 6.6 s on waking.
* **Level within a second.** Two recorders on one laptop, one feed, 2553
  identical frames: |Δ| p90 86 ms, max 1.38 s, one process first 64% of the
  time. That is our own receive jitter, so a lead inside 1 s says nothing about
  a bookmaker. The zero test on those two logs: 3247 moves paired, median
  −3 ms, 99.2% level, no leader.

A lag longer than the window is not seen: the pairing takes the neighbouring
move instead and reports a small lag. Every pair is therefore rerun under a
doubled window, with a warning when the answer moves.
