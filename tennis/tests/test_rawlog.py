"""The raw log is the only thing standing between a parser bug and a lost day,
so its guarantees get tests rather than trust."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.ids import event_id_time_ms, new_event_id, payload_hash
from tennis.ingest.rawlog import RawLog, find_logs, read_raw


@pytest.fixture(params=[True, False], ids=["gzip", "plain"])
def compress(request):
    """Every guarantee below is asserted on both write paths.

    Compression is on by default in production, so testing only the plain path
    would leave the shipped one uncovered.
    """
    return request.param


def test_roundtrip_bytes_and_text(tmp_path, compress):
    clock = FakeClock()
    with RawLog(tmp_path, provider="betboom", clock=clock, fsync_every=0,
                compress=compress) as log:
        log.write(b"\x82\x01\x11binary", channel="tree_ws")
        clock.advance(0.25)
        log.write('{"hello":"мир"}', channel="tree_ws", ts_source_ns=123)
    files = find_logs(tmp_path)
    assert len(files) == 1, files
    assert files[0].name.endswith(".jsonl.gz" if compress else ".jsonl")
    rows = [r for r in read_raw(files[0]) if r["channel"] == "tree_ws"]
    assert rows[0]["payload"] == b"\x82\x01\x11binary"
    assert rows[1]["payload"] == '{"hello":"мир"}'
    assert rows[1]["ts_source_ns"] == 123
    assert rows[0]["sha256"] == payload_hash(b"\x82\x01\x11binary")


def test_compression_is_the_default():
    """The shipped path is the compressed one; forgetting it costs ~1.6 GB a day."""
    log = RawLog("unused", provider="p", clock=FakeClock())
    assert log.compress is True


def test_gzip_output_is_actually_smaller(tmp_path):
    """The feed repeats itself, which is the whole reason for compressing."""
    payload = json.dumps({"market": "2-й сет 6-й гейм: Точный счёт",
                          "outcomes": [{"name": f"П1:{i}", "factor": 1.5} for i in range(8)]})
    sizes = {}
    for flag in (True, False):
        root = tmp_path / f"c{flag}"
        with RawLog(root, provider="p", clock=FakeClock(), compress=flag) as log:
            for _ in range(200):
                log.write(payload, channel="tree_ws")
        sizes[flag] = find_logs(root)[0].stat().st_size
    assert sizes[True] * 4 < sizes[False], sizes


def test_partitioned_by_provider_and_utc_day(tmp_path, compress):
    clock = FakeClock(wall_ns=1_700_000_000_000_000_000)
    with RawLog(tmp_path, provider="betboom", clock=clock, compress=compress) as log:
        log.write("a", channel="c")
        clock.advance(36 * 3600)          # across a UTC midnight
        log.write("b", channel="c")
    days = sorted(p.name for p in (tmp_path / "provider=betboom").iterdir())
    assert len(days) == 2 and all(d.startswith("date=") for d in days)


def test_append_never_truncates(tmp_path, compress):
    """A restarted run must extend its file, not overwrite it. This is the
    one bug that would silently destroy a day of capture.

    For a compressed log this also asserts that a second gzip member appended
    to the same file is read back transparently -- the property that makes
    append-only and compression compatible at all.
    """
    clock = FakeClock()
    log = RawLog(tmp_path, provider="p", clock=clock, compress=compress)
    log.open(); log.write("first", channel="c"); log.close()
    path = find_logs(tmp_path)[0]
    before = sum(1 for _ in read_raw(path))

    log2 = RawLog(tmp_path, provider="p", clock=clock, compress=compress)
    log2.run_id = log.run_id                      # same run id -> same path
    log2.open(); log2.write("second", channel="c"); log2.close()
    payloads = [r.get("payload") for r in read_raw(path)]
    assert sum(1 for _ in read_raw(path)) > before
    assert "first" in payloads and "second" in payloads


def test_torn_last_line_is_skipped_not_fatal(tmp_path):
    with RawLog(tmp_path, provider="p", clock=FakeClock(), compress=False) as log:
        log.write("good", channel="c")
    path = find_logs(tmp_path)[0]
    with open(path, "a") as fh:
        fh.write('{"event_id":"trunc","pay')      # killed mid-write
    rows = list(read_raw(path))
    assert any(r.get("payload") == "good" for r in rows)


def test_truncated_gzip_member_yields_what_survived(tmp_path):
    """A process killed mid-write leaves a member with no trailer.

    The decompressor raises at that point. Everything written before the last
    fsync must still come back, and the error must end iteration rather than
    propagate -- otherwise one hard kill makes a whole day unreadable.
    """
    clock = FakeClock()
    log = RawLog(tmp_path, provider="p", clock=clock, fsync_every=0)
    log.open()
    for i in range(50):
        log.write(f"frame-{i}", channel="c")
        clock.advance(0.01)
    log._flush_sink()                 # everything so far is on disk...
    path = find_logs(tmp_path)[0]
    data = path.read_bytes()
    log._raw.close()                  # ...and then the process dies: no trailer
    log._sink = log._gz = log._raw = None
    path.write_bytes(data[:-3])       # and the tail of the last block is lost

    payloads = [r.get("payload") for r in read_raw(path)]
    assert "frame-0" in payloads
    assert len(payloads) > 40, payloads
    assert not any(p is None for p in payloads)


def test_a_file_that_is_not_gzip_at_all_does_not_raise(tmp_path):
    path = tmp_path / "bogus.jsonl.gz"
    path.write_bytes(b"this was never gzip")
    assert list(read_raw(path)) == []


def test_find_logs_sees_both_extensions(tmp_path):
    for flag in (True, False):
        with RawLog(tmp_path / f"c{flag}", provider="p", clock=FakeClock(),
                    compress=flag) as log:
            log.write("x", channel="c")
    names = [p.name for p in find_logs(tmp_path)]
    assert any(n.endswith(".jsonl.gz") for n in names)
    assert any(n.endswith(".jsonl") and not n.endswith(".gz") for n in names)


def test_event_ids_sort_by_time(tmp_path):
    ids = [new_event_id(1_700_000_000_000_000_000 + i * 10_000_000) for i in range(50)]
    assert ids == sorted(ids)
    assert event_id_time_ms(ids[0]) == 1_700_000_000_000


def test_run_start_and_end_recorded(tmp_path, compress):
    clock = FakeClock()
    with RawLog(tmp_path, provider="p", clock=clock, compress=compress) as log:
        clock.advance(2.0)
        log.write("x", channel="c")
    path = find_logs(tmp_path)[0]
    kinds = []
    for r in read_raw(path):
        if r["channel"] == "_run":
            kinds.append(json.loads(r["payload"])["kind"])
    assert kinds == ["run_start", "run_end"]
