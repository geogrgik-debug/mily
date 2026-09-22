# `tennis.market` — reading the bookmaker's board

Results from this module are in
[`docs/MARKET_MARGINS_2026-09-22.md`](../../docs/MARKET_MARGINS_2026-09-22.md).

| Module | What |
|---|---|
| `names.py` | `market_name` → `MarketRef(scope, set_no, game_no, kind)`, and outcome names → structure |
| `overround.py` | Book sum, margin, proportional normalisation, Shin |
| `fit.py` | The single point probability behind an eight-outcome game book |
| `measure.py` | CLI: margins and fit quality from a captured raw log |

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
