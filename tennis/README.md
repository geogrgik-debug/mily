# tennis/

The build. `research/` stays the laboratory journal; nothing here imports it.
Code in this package must be importable with no side effects, must keep I/O out
of the probability core, and must be covered by tests.

Two tracks run in parallel, because they block on different things.

**Track A, the collector**, has one governing rule: live data cannot be
re-fetched. A game market exists for about a minute and is archived by nobody.
So raw bytes land on disk before anything tries to parse them, and nothing is
ever rewritten — corrections are appended.

**Track B, the engine**, is buildable today from data we already understand, and
starts at the probability core.

| Module | What | Status |
|---|---|---|
| `markov/` | Point → game → tiebreak → set → match, the in-game hold probability, the eight-outcome game market, and the Klaassen–Magnus inversion. See its README. | done, 299 tests |
| `ingest/clock.py` | Paired wall/monotonic readings. The project's decisive number is a latency, so the clock is handled deliberately. | done |
| `ingest/ids.py` | ULIDs (time-sortable event ids) and payload fingerprints. | done |
| `ingest/names.py` | Cross-provider match identity: `initial.surname` normalisation and match keys. | done |
| `ingest/rawlog.py` | Append-only, crash-safe raw frame log. Gzipped by default -- 4.8x measured on live capture -- with the per-line crash guarantee preserved through a Z_SYNC_FLUSH before every fsync. | done |
| `ingest/betboom/` | The BetBoom line recorder. See its README. | done |
| `ratings/` | Elo and as-of priors, with serialisable state so the live process loads ratings instead of recomputing 270k matches. | next |
| `market/` | Market-name parsing, overround, Shin, and the single-p fit behind a game book. Measured: the bookmaker prices the *next* game 74% of the time. See its README. | done |
| `state/` `features/` `models/` `replay/` `eval/` | Live match state, snapshot features, residual model, replay backtest, metrics. | not started |

```bash
pip install pytest numpy
python -m pytest tennis/ -q
```
