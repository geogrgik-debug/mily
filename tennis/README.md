# tennis/

The build. `research/` stays the laboratory journal; nothing here imports it.

Track A of the plan is the collector, and its first rule is that live data
cannot be re-fetched: a game market exists for about a minute and is archived by
nobody. So raw bytes land on disk before anything tries to parse them, and
nothing is ever rewritten — corrections are appended.

| Module | What |
|---|---|
| `ingest/clock.py` | Paired wall/monotonic readings. The project's decisive number is a latency, so the clock is handled deliberately. |
| `ingest/ids.py` | ULIDs (time-sortable event ids) and payload fingerprints. |
| `ingest/names.py` | Cross-provider match identity: `initial.surname` normalisation and match keys. |
| `ingest/rawlog.py` | Append-only, crash-safe raw frame log. |
| `ingest/betboom/` | The BetBoom line recorder. See its README. |

```bash
python -m pytest tennis/tests -q
```
