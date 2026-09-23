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
| `ingest/onewin/` | The 1win recorder: config with no baked-in host, the three endpoints known to answer, a probe. The odds channel waits for one devtools request; see its README. | skeleton |
| `ratings/` | Elo and as-of serve priors (Elo inversion blended with Barnett–Clarke), configured from the B1c sweep, with a snapshot the live process loads in 0.12 s instead of replaying 270k matches. Elo bit-identical to `research/elo_prior.py`; B1 numbers reproduced to four decimals. See its README. | done |
| `market/` | Market-name parsing, overround, Shin, and the single-p fit behind a game book. Measured: the bookmaker prices the *next* game 74% of the time. `lead_lag.py`: which of two books moves a price first, by how much, and whether it is always the same one -- from logs of one machine only. See its README. | done |
| `model/` | Track B step 3: one row per service game from only what was known before it (`game_rows.py`). Leak safety is by construction -- a game enters the accumulators only after its own row is out -- and a test poisons the future to guard it. The serve features shrink with the live state's n0. | step 3 done |
| `state/` | Track B step 4, v1: for each player a Beta on the chance of winning a point on his own serve, centred on the prior with n0 phantom points fitted on history (Slams 140, tour 100, Challenger 90); `p_hold_next` and `p_hold_now` through `markov`. See its README. | v1 done |
| `eval/` | The history measurement: Grand Slam point by point and `tennis_pointbypoint`, priced with `RatingsSnapshot.prior`; n0 fitted on training years, the gain over the prior measured on later ones (+0.0036 Slams, +0.0043 tour, +0.0068 Challenger). `python -m tennis.eval download` then `live-state`. See its README. | done |
| `features/` `replay/` | Snapshot features, residual model, replay backtest. | not started |

```bash
pip install pytest numpy
python -m pytest tennis/ -q
```
