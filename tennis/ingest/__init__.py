"""Append-only capture of live score and odds data.

Design rules, in force everywhere in this package:

1. Raw payloads are stored verbatim before anything is parsed. Live data
   cannot be re-fetched, so a parser bug must never be able to destroy a
   day of capture. Every parsed row carries the `raw_id` it came from.
2. Nothing is ever rewritten. Score corrections are appended as new events
   that name the event they revise.
3. Every event carries both `ts_source` (the provider's clock) and
   `ts_received` (ours), plus a monotonic reading, because the latency
   measurement that decides whether this project can run live is the
   difference between them.
"""

from tennis.ingest.clock import Clock, Instant
from tennis.ingest.ids import new_event_id, payload_hash
from tennis.ingest.names import match_key, normalize_player

__all__ = [
    "Clock",
    "Instant",
    "new_event_id",
    "payload_hash",
    "match_key",
    "normalize_player",
]
