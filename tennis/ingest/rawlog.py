"""Append-only raw frame log.

The first rule of this collector: bytes land on disk before anything tries to
understand them. Live game markets exist for about a minute each and are
archived by nobody, so a parser bug found in week three must not have cost us
weeks one and two. Everything else — parsed stakes, score rows, the latency
tables — is derived, and can be rebuilt by replaying this log.

Format is JSONL, one frame per line, because a line is the unit an append
either fully survives or does not: a process killed mid-write loses at most
the last line, and the reader skips it. Parquet is the *compacted* form, built
later from closed day files; it is not the write path.

Nothing here ever rewrites. Corrections are appended.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from tennis.ingest.clock import Clock, Instant
from tennis.ingest.ids import new_event_id, new_run_id, payload_hash


class RawLog:
    """Crash-safe append-only sink for raw provider frames.

    Usage::

        with RawLog("data/raw", provider="betboom") as log:
            log.write(payload, direction="rx", channel="tree_ws")

    `fsync_every` trades durability against write cost. The default of 1
    second bounds a crash to one second of frames; set it to 0 to fsync every
    frame while recording something irreplaceable and rare.
    """

    def __init__(self, root: str | Path, *, provider: str,
                 clock: Clock | None = None, fsync_every: float = 1.0):
        self.root = Path(root)
        self.provider = provider
        self.clock = clock or Clock()
        self.fsync_every = fsync_every
        self.run_id = new_run_id(self.clock.now().wall_ns)
        self._lock = threading.Lock()
        self._fh = None
        self._day = None
        self._last_fsync = 0.0
        self.frames = 0
        self.bytes = 0
        self._start = self.clock.now()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "RawLog":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        self._write_meta("run_start", {
            "pid": os.getpid(),
            # Both clocks at start, so a wall-clock step mid-run stays
            # recoverable: within a run, monotonic deltas are authoritative.
            "wall_ns": self._start.wall_ns,
            "mono_ns": self._start.mono_ns,
        })

    def close(self) -> None:
        if self._fh is not None:
            self._write_meta("run_end", {
                "frames": self.frames,
                "bytes": self.bytes,
                "seconds": self.clock.now() - self._start,
            })
            with self._lock:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
                self._fh = None

    # -- writing -----------------------------------------------------------

    def _path_for(self, at: Instant) -> tuple[Path, str]:
        day = datetime.fromtimestamp(at.wall_ns / 1e9, timezone.utc).strftime("%Y-%m-%d")
        return self.root / f"provider={self.provider}" / f"date={day}" / f"{self.run_id}.jsonl", day

    def _fh_for(self, at: Instant):
        path, day = self._path_for(at)
        if self._fh is None or day != self._day:
            if self._fh is not None:
                self._fh.flush(); os.fsync(self._fh.fileno()); self._fh.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode: a run that is restarted onto the same path extends
            # it rather than truncating it. Opening 'w' here would be the one
            # bug that silently destroys a day.
            self._fh = open(path, "a", encoding="utf-8")
            self._day = day
        return self._fh

    def write(self, payload: bytes | str, *, direction: str = "rx",
              channel: str = "", ts_source_ns: int | None = None,
              meta: dict | None = None) -> str:
        """Append one frame. Returns its event id.

        `ts_source_ns` is the provider's own timestamp when the frame carries
        one. It is stored beside ours rather than instead of it: their
        difference is the latency this project is trying to measure.
        """
        at = self.clock.now()
        eid = new_event_id(at.wall_ns)
        is_bytes = isinstance(payload, (bytes, bytearray))
        row = {
            "event_id": eid,
            "run_id": self.run_id,
            "provider": self.provider,
            "channel": channel,
            "dir": direction,
            "ts_received_ns": at.wall_ns,
            "ts_mono_ns": at.mono_ns,
            "ts_source_ns": ts_source_ns,
            "enc": "b64" if is_bytes else "utf8",
            "n": len(payload),
            "sha256": payload_hash(payload),
            "payload": base64.b64encode(payload).decode() if is_bytes else payload,
        }
        if meta:
            row["meta"] = meta
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            fh = self._fh_for(at)
            fh.write(line + "\n")
            self.frames += 1
            self.bytes += len(payload)
            elapsed = at.mono_ns / 1e9
            if self.fsync_every <= 0 or elapsed - self._last_fsync >= self.fsync_every:
                fh.flush(); os.fsync(fh.fileno())
                self._last_fsync = elapsed
        return eid

    def _write_meta(self, kind: str, data: dict) -> None:
        self.write(json.dumps({"kind": kind, **data}, ensure_ascii=False),
                   direction="meta", channel="_run")


def read_raw(path: str | Path):
    """Replay a raw log, skipping a torn final line.

    A truncated last line is the expected shape of a crash, not corruption of
    the file, so it is dropped rather than raising.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("enc") == "b64":
                row["payload"] = base64.b64decode(row["payload"])
            yield row
