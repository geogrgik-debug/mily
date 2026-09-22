"""The raw-log inspector, and the recorder diagnostics it complements.

A first live session ended with 51 frames on disk, zero subscriptions and no
output at all: the recorder only spoke on a dropped connection or a market
inventory, and the inventory printed every 200 stake updates, so a count of
zero -- the case most needing an explanation -- printed nothing. These tests
pin the two fixes: the log can be read back and classified without guessing at
field names, and the recorder reports server errors and response types instead
of swallowing them.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest/betboom/generated"))

pb = pytest.importorskip(
    "bb_sport_ws_v1_pb2",
    reason="generated protobuf classes missing; build them with grpc_tools.protoc "
           "(see tennis/ingest/betboom/README.md)",
)

from tennis.ingest.betboom import inspect_log  # noqa: E402
from tennis.ingest.betboom.client import BetBoomRecorder  # noqa: E402


@pytest.fixture
def recorded(tmp_path):
    """A log holding one of each frame shape a live session produces."""
    root = tmp_path / "raw"
    with RawLog(root, provider="betboom", clock=FakeClock()) as log:
        req = pb.MainRequest()
        req.state_subscribe_by_sports.uid = "tree-1"
        req.state_subscribe_by_sports.types.append(pb.TREE_TYPES_LIVE)
        log.write(req.SerializeToString(), direction="tx", channel="tree_ws",
                  meta={"tag": "state_subscribe_by_sports"})

        tree = pb.MainResponse()
        state = tree.state_subscribe_by_sports.states.add()
        sport = state.sports.add()
        sport.info.id = 2
        sport.info.name = "Теннис"
        sport.info.url_slug = "tennis"
        tour = sport.tournaments.add()
        tour.info.id = 40349
        tour.info.name = "Hangzhou"
        tour.matches.add().info.id = 40354739
        log.write(tree.SerializeToString(), direction="rx", channel="tree_ws")

        stake = pb.MainResponse()
        stake.newsletters_stake.stake.stake_id = "s1"
        stake.newsletters_stake.stake.factor = 1.85
        stake.newsletters_stake.stake.market_name = "Победитель гейма"
        stake.newsletters_stake.stake.period_name = "9-й гейм"
        log.write(stake.SerializeToString(), direction="rx", channel="tree_ws")

        for kind in ("newsletters_state_await", "newsletters_state_ready"):
            msg = pb.MainResponse()
            getattr(msg, kind).SetInParent()
            log.write(msg.SerializeToString(), direction="rx", channel="tree_ws")

        err = pb.MainResponse()
        err.error.SetInParent()
        log.write(err.SerializeToString(), direction="rx", channel="tree_ws")

        log.write(b"\xff\xfe\xfd not protobuf at all",
                  direction="rx", channel="tree_ws")
    return root


def test_inspector_classifies_every_frame(recorded, capsys):
    assert inspect_log.main([str(recorded)]) == 0
    out = capsys.readouterr().out
    assert "state_subscribe_by_sports" in out
    assert "newsletters_stake" in out
    assert "newsletters_state_await" in out
    assert "newsletters_state_ready" in out
    assert "error" in out
    assert "FAILED to parse as MainResponse" in out


def test_inspector_walks_deep_enough_to_reach_matches(recorded, capsys):
    """The live tree is about five levels; a shallow walk hides the answer."""
    inspect_log.main([str(recorded)])
    out = capsys.readouterr().out
    assert "Теннис" in out
    assert "Hangzhou" in out
    assert "40354739" in out


def test_inspector_reports_requests_we_sent(recorded, capsys):
    inspect_log.main([str(recorded)])
    out = capsys.readouterr().out
    assert "requests we sent" in out
    assert "state_subscribe_by_sports" in out


def test_inspector_can_filter_to_one_type(recorded, capsys):
    inspect_log.main([str(recorded), "--type", "newsletters_stake"])
    out = capsys.readouterr().out
    assert "Победитель гейма" in out
    assert "--- state_subscribe_by_sports ---" not in out


def test_inspector_explains_a_missing_path(tmp_path, capsys):
    assert inspect_log.main([str(tmp_path / "nope")]) == 1
    assert "nothing at" in capsys.readouterr().err


def test_inspector_survives_a_log_of_only_garbage(tmp_path, capsys):
    root = tmp_path / "raw"
    with RawLog(root, provider="betboom", clock=FakeClock()) as log:
        log.write(b"\x00\x01\x02", direction="rx", channel="tree_ws")
    assert inspect_log.main([str(root)]) == 0
    assert "FAILED to parse" in capsys.readouterr().out


# --------------------------------------------------- recorder diagnostics


def _recorder(tmp_path, **kw):
    log = RawLog(tmp_path / "raw", provider="betboom", clock=FakeClock())
    log.open()
    return BetBoomRecorder(log, discover=True, **kw)


def test_recorder_surfaces_a_server_error(tmp_path, capsys):
    """A refused subscription used to look exactly like an idle socket."""
    import asyncio

    rec = _recorder(tmp_path)
    msg = pb.MainResponse()
    msg.error.SetInParent()
    asyncio.run(rec._handle(None, msg))
    assert rec.errors, "the error was swallowed"
    assert "[error]" in capsys.readouterr().err
    assert rec.kinds["error"] == 1


def test_recorder_counts_response_types(tmp_path):
    import asyncio

    rec = _recorder(tmp_path)
    for kind in ("newsletters_state_await", "newsletters_state_ready"):
        msg = pb.MainResponse()
        getattr(msg, kind).SetInParent()
        asyncio.run(rec._handle(None, msg))
    assert rec.kinds["newsletters_state_await"] == 1
    assert rec.kinds["newsletters_state_ready"] == 1


def test_report_says_so_when_no_tree_arrived(tmp_path, capsys):
    rec = _recorder(tmp_path)
    rec.report()
    err = capsys.readouterr().err
    assert "no sports tree arrived" in err
    assert "subscribed to 0 match(es)" in err


def test_report_lists_sports_with_their_match_counts(tmp_path, capsys):
    """An empty tree and a missing tree are different diagnoses."""
    import asyncio

    rec = _recorder(tmp_path)
    msg = pb.MainResponse()
    state = msg.state_subscribe_by_sports.states.add()
    sport = state.sports.add()
    sport.info.id = 2
    sport.info.name = "Теннис"
    sport.info.url_slug = "tennis"
    sport.info.matches_count = 42          # the server claims 42 live matches
    asyncio.run(rec._handle(None, msg))    # but sends none inline
    rec.report()
    err = capsys.readouterr().err
    assert "sports in the tree" in err
    assert "tournaments=   0" in err
    assert "matches=    0" in err
