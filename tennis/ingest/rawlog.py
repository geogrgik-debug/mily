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

Written gzipped by default, because the feed repeats itself: BetBoom reprices
by resending every stake on a match, so a minute of six matches is 2.1 MB of
raw JSONL that gzip takes to 394 KB -- 5.4x on that sample, 4.8x on a live WTT
capture. For six matches that turns ~3 GB a day into ~0.55 GB. The line-level crash guarantee survives
compression: every fsync is preceded by a zlib Z_SYNC_FLUSH, so bytes already
written stay decodable, and a reader that hits a torn final member stops there
with everything before it intact. `compress=False` writes plain JSONL when a
file needs to be readable by eye or by `grep`.

Nothing here ever rewrites. Corrections are appended.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import threading
import zlib
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

    `compress` writes `.jsonl.gz` instead of `.jsonl`. It is on by default:
    the saving is 4.8-5.4x on real captures and the crash guarantee is unchanged,
    because each fsync is preceded by a Z_SYNC_FLUSH. Turn it off only when a
    human or `grep` needs to read the file directly.
    """

    def __init__(self, root: str | Path, *, provider: str,
                 clock: Clock | None = None, fsync_every: float = 1.0,
                 compress: bool = True):
        self.root = Path(root)
        self.provider = provider
        self.clock = clock or Clock()
        self.fsync_every = fsync_every
        self.compress = compress
        self.run_id = new_run_id(self.clock.now().wall_ns)
        self._lock = threading.Lock()
        self._sink = None          # what lines are written to (bytes)
        self._gz = None            # the GzipFile, when compressing
        self._raw = None           # the OS-level file, the thing we fsync
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
        if self._sink is not None:
            self._write_meta("run_end", {
                "frames": self.frames,
                "bytes": self.bytes,
                "seconds": self.clock.now() - self._start,
            })
            with self._lock:
                self._close_sink()

    # -- writing -----------------------------------------------------------

    def _path_for(self, at: Instant) -> tuple[Path, str]:
        day = datetime.fromtimestamp(at.wall_ns / 1e9, timezone.utc).strftime("%Y-%m-%d")
        suffix = ".jsonl.gz" if self.compress else ".jsonl"
        return (self.root / f"provider={self.provider}" / f"date={day}"
                / f"{self.run_id}{suffix}"), day

    def _sink_for(self, at: Instant):
        path, day = self._path_for(at)
        if self._sink is None or day != self._day:
            self._close_sink()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode: a run that is restarted onto the same path extends
            # it rather than truncating it. Opening 'w' here would be the one
            # bug that silently destroys a day. For gzip this appends a second
            # member, which every gzip reader concatenates transparently.
            self._raw = open(path, "ab")
            if self.compress:
                # mtime from our own clock, not the wall: a FakeClock run then
                # produces byte-identical output, which is what makes the
                # compressed path testable at all.
                self._gz = gzip.GzipFile(fileobj=self._raw, mode="ab",
                                         mtime=int(at.wall_ns // 1_000_000_000))
                self._sink = self._gz
            else:
                self._gz = None
                self._sink = self._raw
            self._day = day
        return self._sink

    def _flush_sink(self) -> None:
        """Make everything written so far readable by another process.

        Z_SYNC_FLUSH ends the current deflate block and pads to a byte
        boundary without resetting the dictionary, so the bytes on disk are a
        decodable prefix and compression barely suffers. Without it a crash
        would lose the whole in-memory deflate window, not one line.
        """
        if self._gz is not None:
            self._gz.flush(zlib.Z_SYNC_FLUSH)
        self._raw.flush()
        os.fsync(self._raw.fileno())

    def _close_sink(self) -> None:
        if self._sink is None:
            return
        self._flush_sink()
        if self._gz is not None:
            self._gz.close()            # writes the gzip trailer
        self._raw.close()
        self._sink = self._gz = self._raw = None

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
            sink = self._sink_for(at)
            sink.write((line + "\n").encode("utf-8"))
            self.frames += 1
            self.bytes += len(payload)
            elapsed = at.mono_ns / 1e9
            if self.fsync_every <= 0 or elapsed - self._last_fsync >= self.fsync_every:
                self._flush_sink()
                self._last_fsync = elapsed
        return eid

    def _write_meta(self, kind: str, data: dict) -> None:
        self.write(json.dumps({"kind": kind, **data}, ensure_ascii=False),
                   direction="meta", channel="_run")


LOG_GLOBS = ("*.jsonl", "*.jsonl.gz")


def find_logs(root: str | Path):
    """Every log file under `root`, compressed or not, in a stable order.

    Callers should use this rather than globbing `*.jsonl` themselves, which
    silently matches nothing once capture is compressed.
    """
    root = Path(root)
    if root.is_file():
        return [root]
    found = []
    for pattern in LOG_GLOBS:
        found.extend(root.rglob(pattern))
    return sorted(set(found))


def read_raw(path: str | Path):
    """Replay a raw log, skipping a torn tail. Handles .jsonl and .jsonl.gz.

    A truncated last line is the expected shape of a crash, not corruption of
    the file, so it is dropped rather than raising. The same applies one layer
    down for a compressed log: a process killed mid-write leaves a gzip member
    without its trailer, and the decompressor raises at that point. Everything
    before it is intact and is yielded; the error ends iteration rather than
    propagating, because losing the last second of a capture is the documented
    cost of the fsync interval, not a failure to report.
    """
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    fh = opener(path, "rb")
    try:
        while True:
            try:
                line = fh.readline()
            except (OSError, EOFError, zlib.error):
                # gzip.BadGzipFile is an OSError; a truncated member can also
                # surface as EOFError or a raw zlib error depending on where
                # the process died.
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if row.get("enc") == "b64":
                row["payload"] = base64.b64decode(row["payload"])
            yield row
    finally:
        fh.close()
