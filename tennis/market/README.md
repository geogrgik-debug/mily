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
| `streams.py` | Raw log → quotes → price events (margin-free probability of an outcome, emitted when it changes), for BetBoom and 1win; refuses logs not written on one machine |
| `join.py` | Which match of one book is which of another, by the players' names; re-keys the second book onto the first |
| `lead_lag.py` | CLI: which of two books moves a price first, by how much, and whether the leader is stable |
| `push_vs_snapshot.py` | CLI: BetBoom's per-stake pushes against its full snapshots -- does a late snapshot put a pushed price back, and how far ahead the push is |
| `p_gap.py` | CLI: how far BetBoom and 1win disagree on p, the server's point probability, game by game, and 1win's margin on the same markets |

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

### Two books: BetBoom and 1win

1win's quotes (`onewin_quotes`) get BetBoom's market addresses for the
markets both quote as two players' odds: the match, a set, a game ("Победитель
гейма", with the set and game as numbers). The match ids and the sides are
each book's own, so `join.py` pairs the matches by the players' names and
re-keys 1win onto BetBoom: 1win's "1" becomes "П1" or "П2" by where that
player is at BetBoom -- home and away need not agree between books.

The names are Russian in both books ("Тимофеева М." against "Мария
Тимофеева"), and `tennis.ingest.names.match_key` folds names to ASCII, which
drops Cyrillic whole ("Медведев Д." gives ""). So the pairing goes by the
Russian words: one word in common, or a spelling apart for words of six
letters and more ("Риналдо"/"Ринальдо"); both players, one to one, and
quoted at overlapping times. A match that pairs with none, or with two, is
left out, and the report says how many.

First reading, 23.09 from 22:41 MSK, fifteen minutes, both recorders on the
owner's laptop:

    python -m tennis.market.lead_lag betboom=data/lab/trial/bb 1win=data/lab/trial/1w

15 of 1win's 20 matches paired; one clock (offsets agree to 0.0 ms); 322
moves paired, 71% of the fewer, and a doubled window did not move the answer.
BetBoom first 265 (82.3%), level 34 (10.6%), 1win first 23 (7.1%); 1win
after BetBoom by a median of +2.06 s, p90 +4.14 s; BetBoom first in 92% of
288 decided moves (95% interval 88-95%) and in 12 of 13 matches: stable.
Fifteen minutes of one evening is a first reading, not the answer -- that
takes both recorders on the capture host for days.

## Pushes against snapshots: `push_vs_snapshot`

    python -m tennis.market.push_vs_snapshot data/vps

BetBoom prices a game market twice: one outcome at a time by push, and in the
full snapshot of the match. `betboom_quotes` differences a snapshot against the
previous snapshot, not against the pushes, so a snapshot still carrying a price
the pushes had left would put it back, and the next change would move it on --
two moves the book never made. Measured on the capture host's logs of 22-23.09
(19.7 h, 12,385 pushed outcomes), that does not happen:

* Of 76,063 snapshot quotes of an outcome already pushed, 63,811 carry its
  latest pushed price and none a price the pushes had left. The other 12,252
  take the outcome off the board before a push says so, which prices nothing.
* The push was first in all 57,638 paired changes: snapshot minus push median
  +0.52 s, p90 +0.61 s, max +10.63 s. The first look, 16 outcomes over 7
  minutes, had a p90 of 78 s; it does not hold.
* Where the snapshot may have been first, the report says so apart: 38 first
  pushes came at a price a snapshot had already moved to, 6.36 to 14.89 s
  after it. Counted as the snapshot's, they leave the push first in 57,638
  of 57,676 changes.

So the meter's stream needs no guard against snapshots, and a game market's
move is timed by its push. Only the capture host has pushes: the laptop's
recorder predates 3f695d8, and the command refuses a capture without them.

## How far two books disagree on p: `p_gap`

    python -m tennis.market.p_gap betboom=PATH 1win=PATH [--games games.csv]

START_HERE carried "two books differ on p by 1.6 points", and nothing in the
repository computed it. This computes it from two books recorded on one
machine, one number per game:

* the game is one both books priced **before it began**, while the game
  before it was played (BetBoom's scoreboard). The price of a game in
  progress depends on its point score, and an inversion from 0-0 does not
  know that;
* the moment is the last one before either book shut the market at which
  both were open and **neither had moved for 10 s** (`--settle`). 1win
  follows BetBoom by about 2 s, so a moment right after a move would measure
  the lag, not a disagreement;
* each book's two-way "winner of the game", margin removed, read as P(the
  server holds). The server is BetBoom's next one, by alternation; p comes
  from inverting `markov.p_game`. Which side serves does not change the size
  of the gap, because p_game(1 - p) = 1 - p_game(p);
* the median of |p 1win - p BetBoom|, with a 95% interval that resamples
  **matches**, not games: twenty games of one match are not twenty witnesses.
  Beside it: the mean signed gap, the gap in P(hold), and both books' margin
  at the same moments.

Two units are easy to mix up here. The 1.09 points between BetBoom's own two
markets (above) is in P(hold); the gap here is in p, and P(hold) is about
1.9 times as sensitive near p = 0.62. The report gives both.

Margin removal moves the answer. On the first two-book trial (laptop, 23.09,
15 minutes, 13 games) Shin gave twice the gap that proportional removal gave.
So `--method proportional` belongs in any report beside the default. The
number itself waits for a day of the capture host's logs, where 1win records
beside BetBoom (`data/raw/provider=1win`).
