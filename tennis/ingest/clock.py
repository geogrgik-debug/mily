"""Clock discipline.

The number that decides whether this project exists as a live system is a
latency: our timestamp minus the provider's, and the bookmaker's repricing
minus the point. Both are measured in tens or hundreds of milliseconds, so
the clock has to be handled deliberately rather than by calling
`time.time()` wherever a timestamp is needed.

Two readings are taken together for every event:

* `wall_ns` — UTC wall clock. Joins across processes and providers, and the
  only clock comparable with a provider's own timestamp. It can jump: NTP
  steps it, containers resume with a stale clock.
* `mono_ns` — monotonic. Never jumps, but is meaningless across processes.
  Orders events within one run and detects wall-clock steps.

Each run writes a `run_anchor` meta event holding the pair at start, so a
wall-clock step mid-run is recoverable after the fact: within a run,
`mono_ns` is authoritative for ordering and for differences.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Instant:
    """One simultaneous reading of both clocks."""

    wall_ns: int
    mono_ns: int

    @property
    def wall_s(self) -> float:
        return self.wall_ns / 1e9

    def __sub__(self, other: "Instant") -> float:
        """Seconds between two instants, measured on the monotonic clock."""
        return (self.mono_ns - other.mono_ns) / 1e9


class Clock:
    """Source of `Instant`s. Injectable so tests can drive time."""

    def now(self) -> Instant:
        # Sampled as close together as the interpreter allows. The residual
        # skew between the two calls is far below the latencies we measure.
        return Instant(wall_ns=time.time_ns(), mono_ns=time.monotonic_ns())

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock(Clock):
    """Deterministic clock for tests."""

    def __init__(self, wall_ns: int = 1_700_000_000_000_000_000, mono_ns: int = 0):
        self._wall = wall_ns
        self._mono = mono_ns
        self.slept = 0.0

    def now(self) -> Instant:
        return Instant(wall_ns=self._wall, mono_ns=self._mono)

    def advance(self, seconds: float, *, wall_step: float = 0.0) -> None:
        """Advance both clocks. `wall_step` additionally jumps the wall clock
        only, simulating an NTP correction."""
        delta = int(seconds * 1e9)
        self._mono += delta
        self._wall += delta + int(wall_step * 1e9)

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.advance(seconds)


def clock_skew_s(ts_source_ns: int | None, ts_received_ns: int) -> float | None:
    """Observed lag of a provider's own timestamp behind our receipt.

    Positive means the event was stamped by the provider before we saw it,
    which is the normal case and the quantity the bake-off reports. Negative
    means the provider's clock runs ahead of ours; that is a clock-offset
    problem, not a negative latency, and is reported as such.
    """
    if ts_source_ns is None:
        return None
    return (ts_received_ns - ts_source_ns) / 1e9
