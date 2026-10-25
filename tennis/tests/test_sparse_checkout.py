"""The capture host's sparse checkout must hold everything the recorder imports.

deploy/README.md puts only part of the repository on the capture machine. When
the recorder started importing `tennis.market` (3f695d8), that checkout stopped
being able to run it -- `No module named 'tennis.market'` -- and nothing here
noticed: in a full checkout every import resolves. It surfaced only when the
runbook was replayed on a real sparse clone. So the sparse set is read out of
the runbook itself and checked against what the host's entry points load.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "deploy" / "README.md"

# What the capture host runs: both services (BetBoom and, from 24.09, 1win
# beside it), the heartbeat's status report, and the log inspector the runbook
# points to when something looks wrong.
ENTRY_POINTS = (
    "tennis.ingest.betboom.client",
    "tennis.ingest.onewin.client",
    "tennis.ingest.status",
    "tennis.ingest.betboom.inspect_log",
)


def sparse_patterns(runbook_text):
    """The quoted paths of the runbook's `git sparse-checkout set` command."""
    m = re.search(r"git sparse-checkout set --no-cone(.*?)\n```", runbook_text, re.S)
    assert m, "deploy/README.md no longer shows a `git sparse-checkout set --no-cone` command"
    return re.findall(r"'(/[^']+)'", m.group(1))


def covered(rel, patterns):
    """Whether a repo-relative posix path is inside the sparse set.

    Only the two pattern shapes the runbook uses are understood: an exact file
    and a directory with `/**`. Anything else fails loudly rather than being
    guessed at.
    """
    for p in patterns:
        p = p.lstrip("/")
        if p.endswith("/**"):
            if rel.startswith(p[:-2]):
                return True
        elif "*" in p or "?" in p or "[" in p:
            raise ValueError(f"pattern shape not understood here: {p!r}")
        elif rel == p:
            return True
    return False


def files_loaded_by(modules):
    """Repo-relative files of every `tennis` module a fresh interpreter loads."""
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in modules)
        + "for m in list(sys.modules.values()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if f and getattr(m, '__name__', '').split('.')[0] == 'tennis':\n"
        "        print(f)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
        encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert out.returncode == 0, out.stderr
    return sorted(Path(line).resolve().relative_to(ROOT).as_posix()
                  for line in out.stdout.splitlines() if line.strip())


def test_the_runbook_names_a_sparse_set():
    patterns = sparse_patterns(RUNBOOK.read_text(encoding="utf-8"))
    assert "/tennis/ingest/**" in patterns
    assert "/deploy/**" in patterns


def test_the_sparse_set_holds_everything_the_capture_host_imports():
    patterns = sparse_patterns(RUNBOOK.read_text(encoding="utf-8"))
    loaded = files_loaded_by(ENTRY_POINTS)
    assert "tennis/ingest/betboom/client.py" in loaded
    assert "tennis/ingest/onewin/client.py" in loaded
    missing =[f for f in loaded if not covered(f, patterns)]
    assert not missing, (
        "the capture host's sparse checkout (deploy/README.md, step 2) lacks "
        f"files the recorder imports: {missing}. Add their directories to the "
        "`git sparse-checkout set` line there and in docs/TRACK_A_betboom_capture.md."
    )


def test_the_check_would_have_caught_the_original_break():
    # The set as it stood before 3f695d8 was accounted for.
    before = ["/tennis/__init__.py", "/tennis/ingest/**", "/tennis/tests/**",
              "/deploy/**", "/requirements.txt", "/conftest.py"]
    assert covered("tennis/ingest/betboom/client.py", before)
    assert not covered("tennis/market/names.py", before)
    assert not covered("tennis/markov/core.py", before)


@pytest.mark.parametrize("rel,expected", [
    ("tennis/__init__.py", True),
    ("tennis/ingest/rawlog.py", True),
    ("tennis/ingestion/x.py", False),   # a prefix of a name is not the directory
    ("research/elo_prior.py", False),
])
def test_covered_matches_whole_directories_only(rel, expected):
    assert covered(rel, ["/tennis/__init__.py", "/tennis/ingest/**"]) is expected
