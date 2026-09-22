# `tennis/` — production modules

`research/` is the lab journal: scripts that were run once, with results pinned in
JSON next to them. `tennis/` is the opposite contract. Code here must be
importable with no side effects, must not do I/O in the probability core, and must
be covered by tests.

Run the suite from the repository root:

```
pip install pytest numpy
python -m pytest tennis/ -q
```

| Module | Status |
|---|---|
| `markov/` | done — probability core, 299 tests |
| `ratings/` | next — Elo and as-of priors with serialisable state |
| `ingest/` | blocked on choosing a feed |
| `market/` | blocked on a recorded odds stream |
| `state/` `features/` `models/` `replay/` `eval/` | not started |
