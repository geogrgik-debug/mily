"""The capture reporter exists to catch a silent failure, so its own failure
modes are what get tested: a dead capture must not read as a healthy one."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog
from tennis.ingest.status import capture_status


def _capture(tmp_path, clock=None):
    root = tmp_path / "raw"
    with RawLog(root, provider="betboom", clock=clock or FakeClock()) as log:
        log.write(b"frame", channel="tree_ws")
    return root


def test_a_live_capture_reads_as_ok(tmp_path):
    root = _capture(tmp_path)
    st = capture_status(root)
    assert st.ok, st.reason
    assert st.files == 1 and st.newest_bytes > 0
    assert st.age_s is not None and st.age_s < 60


def test_a_missing_directory_is_not_ok(tmp_path):
    st = capture_status(tmp_path / "never-created")
    assert not st.ok
    assert "нет каталога" in st.reason


def test_an_empty_directory_is_not_ok(tmp_path):
    (tmp_path / "raw").mkdir()
    st = capture_status(tmp_path / "raw")
    assert not st.ok
    assert "не начиналась" in st.reason


def test_a_stale_capture_is_not_ok(tmp_path):
    """The failure this whole module exists for: the process died quietly."""
    root = _capture(tmp_path)
    log = next(iter(root.rglob("*.jsonl.gz")))
    old = time.time() - 3600
    import os
    os.utime(log, (old, old))
    st = capture_status(root)
    assert not st.ok
    assert "встала" in st.reason
    assert st.age_s > 3000


def test_almost_full_disk_is_not_ok(tmp_path):
    """Free space is reported in days of recording, not bytes -- that is the
    number that tells you whether to act."""
    root = _capture(tmp_path)
    st = capture_status(root, bytes_per_day=1e18)     # pretend the disk is tiny
    assert not st.ok
    assert "осталось на" in st.reason
    assert st.disk_free_days is not None


def test_report_is_json_and_carries_every_field(tmp_path):
    import json
    st = capture_status(_capture(tmp_path))
    data = json.loads(st.to_json())
    for key in ("ok", "reason", "checked_at_s", "newest_file", "newest_bytes",
                "age_s", "files", "total_bytes", "disk_free_bytes", "disk_free_days"):
        assert key in data, key


def test_cli_writes_atomically_and_sets_the_exit_code(tmp_path, capsys):
    from tennis.ingest.status import main
    root = _capture(tmp_path)
    out = tmp_path / "status" / "host.json"
    assert main([str(root), "--out", str(out)]) == 0
    assert out.exists() and '"ok": true' in out.read_text()
    assert not list(out.parent.glob("*.tmp")), "temp file left behind"
    assert main([str(tmp_path / "gone")]) == 1


def _sidecar(root, run_id="r", **fields):
    import json, time
    base = {"run_id": run_id, "written_at_s": time.time(), "frames": 1000,
            "subscribed": 10, "stakes_seen": 5000,
            "last_stake_at_s": time.time() - 5, "reconnects": 0,
            "errors": 0, "bad_codes": 0}
    base.update(fields)
    (root / f"_recorder-{run_id}.json").write_text(json.dumps(base))


def test_growing_file_with_no_subscriptions_is_not_ok(tmp_path):
    """The blind spot the first version had: a lost subscription still writes.

    Reproduced from a real capture: 0 subscribed, stakes frozen, log growing
    on the tour-wide score stream, and the file-only check said fine.
    """
    root = _capture(tmp_path)
    _sidecar(root, subscribed=0, reconnects=1)
    st = capture_status(root)
    assert not st.ok
    assert "НЕ ПОДПИСАН" in st.reason
    assert st.subscribed == 0 and st.reconnects == 1


def test_growing_file_with_stale_prices_is_not_ok(tmp_path):
    import time
    root = _capture(tmp_path)
    _sidecar(root, subscribed=10, last_stake_at_s=time.time() - 3600)
    st = capture_status(root)
    assert not st.ok
    assert "котировок нет" in st.reason
    assert st.last_stake_age_s > 3000


def test_healthy_sidecar_reports_subscriptions_and_prices(tmp_path):
    root = _capture(tmp_path)
    _sidecar(root, subscribed=10, stakes_seen=68412)
    st = capture_status(root)
    assert st.ok, st.reason
    assert "10 подписок" in st.reason and "68412 котировок" in st.reason
    assert st.last_stake_age_s is not None and st.last_stake_age_s < 60


def test_without_a_sidecar_the_verdict_says_it_is_file_only(tmp_path):
    """An older recorder leaves no counters; the check must say so rather
    than silently downgrade to the weaker test."""
    st = capture_status(_capture(tmp_path))
    assert st.ok
    assert "только по файлу" in st.reason
    assert st.subscribed is None


def test_a_corrupt_sidecar_is_ignored_not_fatal(tmp_path):
    root = _capture(tmp_path)
    (root / "_recorder-x.json").write_text("{not json")
    st = capture_status(root)
    assert st.ok and st.subscribed is None


def test_a_dead_probes_sidecar_does_not_speak_for_the_live_capture(tmp_path):
    """Observed live: a two-minute --discover probe exited and left its status
    file; the hour-old real capture kept writing beside it. One shared sidecar
    name meant the checker would have called the live capture dead."""
    import time
    root = _capture(tmp_path)
    _sidecar(root, run_id="probe", subscribed=3, stakes_seen=40,
             written_at_s=time.time() - 3600)            # exited an hour ago
    _sidecar(root, run_id="live", subscribed=10, stakes_seen=68412)
    st = capture_status(root)
    assert st.ok, st.reason
    assert st.subscribed == 10 and st.stakes_seen == 68412


def test_only_a_stale_sidecar_is_ignored_not_trusted(tmp_path):
    """A crashed run's file must not make a still-writing capture look dead,
    nor be mistaken for proof that it is alive."""
    import time
    root = _capture(tmp_path)
    _sidecar(root, run_id="crashed", subscribed=0, written_at_s=time.time() - 3600)
    st = capture_status(root)
    assert st.ok                       # the log itself is fresh
    assert st.subscribed is None       # and no live counters were claimed
    assert "только по файлу" in st.reason
