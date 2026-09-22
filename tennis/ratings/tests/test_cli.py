"""The command line end to end, on synthetic files."""
import json

import pytest

from tennis.ratings.__main__ import main
from tennis.ratings.tests.synthetic import generate, write_files


@pytest.fixture(scope="module")
def atp(tmp_path_factory):
    d = tmp_path_factory.mktemp("atp")
    write_files(d, generate(years=range(2020, 2023), weeks=12))
    return d


def test_build_then_prior(atp, tmp_path, capsys):
    out = tmp_path / "snap.json.gz"
    assert main(["build", str(atp), "--out", str(out)]) == 0
    assert "matches through" in capsys.readouterr().out
    assert main(["prior", str(out), "Player 01", "Player 02", "--surface", "Clay",
                 "--best-of", "3", "--as-of", "2023-09-04"]) == 0
    text = capsys.readouterr().out
    assert "serve prior" in text and "Player 01 vs Player 02" in text


def test_an_ambiguous_name_is_refused(atp, tmp_path):
    out = tmp_path / "snap.json"
    main(["build", str(atp), "--out", str(out)])
    with pytest.raises(SystemExit, match="matches"):
        main(["prior", str(out), "Player", "Player 02", "--surface", "Clay", "--as-of", "2023-09-04"])


def test_evaluate_writes_the_report(atp, tmp_path, capsys):
    js = tmp_path / "r.json"
    assert main(["evaluate", str(atp), "--train-until", "2021", "--json", str(js)]) == 0
    res = json.loads(js.read_text())
    assert res["n_test"] > 0 and 0 <= res["blend_weight_elo"] <= 1
    assert set(res["rmse"]) >= {"p_const", "p_raw", "p_bc", "p_elo", "p_blend", "p_blend_config"}
    assert "p_blend" in capsys.readouterr().out
