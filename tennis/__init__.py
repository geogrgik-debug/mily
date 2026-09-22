"""Live tennis collector and modelling code.

`research/` stays the laboratory journal; nothing here imports it. Everything
under `tennis/` is code the live engine is allowed to import: no module-level
work, no I/O in the probability core, and covered by tests.

Track A of the plan (the collector) lives in `tennis.ingest`.
Track B starts at `tennis.markov` (the probability core).
"""

__all__ = ["ingest", "markov"]
