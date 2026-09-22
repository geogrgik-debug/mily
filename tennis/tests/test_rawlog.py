"""The raw log is the only thing standing between a parser bug and a lost day,
so its guarantees get tests rather than trust."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.ids import event_id_time_ms, new_event_id, payload_hash
from tennis.ingest.rawlog import RawLog, read_raw


def test_roundtrip_bytes_and_text(tmp_path):
    clock = FakeClock()
    with RawLog(tmp_path, provider="betboom", clock=clock, fsync_every=0) as log:
        log.write(b"\x82\x01\x11binary", channel="tree_ws")
        clock.advance(0.25)
        log.write('{"hello":"мир"}', channel="tree_ws", ts_source_ns=123)
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1, files
    rows = [r for r in read_raw(files[0]) if r["channel"] == "tree_ws"]
    assert rows[0]["payload"] == b"\x82\x01\x11binary"
    assert rows[1]["payload"] == '{"hello":"мир"}'
    assert rows[1]["ts_source_ns"] == 123
    assert rows[0]["sha256"] == payload_hash(b"\x82\x01\x11binary")


def test_partitioned_by_provider_and_utc_day(tmp_path):
    clock = FakeClock(wall_ns=1_700_000_000_000_000_000)
    with RawLog(tmp_path, provider="betboom", clock=clock) as log:
        log.write("a", channel="c")
        clock.advance(36 * 3600)          # across a UTC midnight
        log.write("b", channel="c")
    days = sorted(p.name for p in (tmp_path / "provider=betboom").iterdir())
    assert len(days) == 2 and all(d.startswith("date=") for d in days)


def test_append_never_truncates(tmp_path):
    """A restarted run must extend its file, not overwrite it. This is the
    one bug that would silently destroy a day of capture."""
    clock = FakeClock()
    log = RawLog(tmp_path, provider="p", clock=clock)
    log.open(); log.write("first", channel="c"); log.close()
    path = next(tmp_path.rglob("*.jsonl"))
    before = path.read_text().count("\n")

    log2 = RawLog(tmp_path, provider="p", clock=clock)
    log2.run_id = log.run_id                      # same run id -> same path
    log2.open(); log2.write("second", channel="c"); log2.close()
    after = path.read_text().count("\n")
    assert after > before
    assert "first" in path.read_text() and "second" in path.read_text()


def test_torn_last_line_is_skipped_not_fatal(tmp_path):
    with RawLog(tmp_path, provider="p", clock=FakeClock()) as log:
        log.write("good", channel="c")
    path = next(tmp_path.rglob("*.jsonl"))
    with open(path, "a") as fh:
        fh.write('{"event_id":"trunc","pay')      # killed mid-write
    rows = list(read_raw(path))
    assert any(r.get("payload") == "good" for r in rows)


def test_event_ids_sort_by_time(tmp_path):
    ids = [new_event_id(1_700_000_000_000_000_000 + i * 10_000_000) for i in range(50)]
    assert ids == sorted(ids)
    assert event_id_time_ms(ids[0]) == 1_700_000_000_000


def test_run_start_and_end_recorded(tmp_path):
    clock = FakeClock()
    with RawLog(tmp_path, provider="p", clock=clock) as log:
        clock.advance(2.0)
        log.write("x", channel="c")
    path = next(tmp_path.rglob("*.jsonl"))
    kinds = []
    for r in read_raw(path):
        if r["channel"] == "_run":
            kinds.append(json.loads(r["payload"])["kind"])
    assert kinds == ["run_start", "run_end"]
