"""Reading the match files: what is dropped, what is defaulted, and the order."""
import numpy as np
import pytest

from tennis.ratings.sackmann import download, fingerprint, from_records, load_matches, match_files
from tennis.ratings.tests.synthetic import generate, write_files


def row(**kw):
    base = {"tourney_id": "2020-1", "tourney_date": "20200106", "tourney_level": "A",
            "match_num": "1", "winner_id": "1", "loser_id": "2", "surface": "Clay",
            "best_of": "3", "src": "tour"}
    base.update(kw)
    return base


def test_rows_without_a_date_or_a_player_are_dropped():
    m = from_records([row(), row(tourney_date=""), row(tourney_date="2020-01-06"),
                      row(winner_id=""), row(loser_id="NA")])
    assert len(m) == 1


def test_missing_values_default_as_pandas_read_them():
    m = from_records([row(surface=""), row(surface="NA", match_num="2"),
                      row(best_of="", match_num="3"), row(best_of="5", match_num="4")])
    assert list(m.surface) == ["Hard", "Hard", "Clay", "Clay"]
    assert list(m.best_of) == [3, 3, 3, 5]


def test_a_float_formatted_date_still_reads():
    assert str(from_records([row(tourney_date="20200106.0")]).date[0]) == "2020-01-06"


def test_order_is_date_then_match_number_with_missing_last_and_stable_ties():
    m = from_records([
        row(tourney_id="b", match_num="2", tourney_date="20200113"),
        row(tourney_id="a", match_num=""),
        row(tourney_id="c", match_num="5"),
        row(tourney_id="d", match_num="5"),
        row(tourney_id="e", match_num="1"),
    ])
    assert list(m.tourney_id) == ["e", "c", "d", "a", "b"]
    assert list(m.match_key) == ["e#0", "c#1", "d#2", "a#3", "b#4"]


def test_files_are_read_tour_first_and_fingerprinted(tmp_path):
    write_files(tmp_path, generate(years=range(2020, 2022), weeks=3))
    names = [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in match_files(tmp_path)]
    assert names == ["atp_matches_2020.csv", "atp_matches_2021.csv",
                     "atp_matches_qual_chall_2020.csv", "atp_matches_qual_chall_2021.csv"]
    m = load_matches(tmp_path)
    assert set(m.src) == {"tour", "chall_qual"}
    assert (np.diff(m.date.astype(np.int64)) >= 0).all()
    fp = fingerprint(tmp_path)
    assert sorted(fp) == names and all(len(h) == 64 for h in fp.values())


def test_an_empty_directory_says_where_the_download_commands_are(tmp_path):
    with pytest.raises(FileNotFoundError, match="EXPERIMENT_B"):
        load_matches(tmp_path)


def test_before_keeps_keys_and_order():
    m = from_records([row(tourney_date="20200106"), row(tourney_date="20200113", match_num="2")])
    early = m.before("2020-01-13")
    assert list(early.match_key) == ["2020-1#0"]


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_fetches_only_what_is_missing(tmp_path):
    asked = []

    def opener(url):
        asked.append(url.rsplit("/", 1)[-1])
        return _Resp(b"tourney_id,tourney_name\n")

    (tmp_path / "atp_matches_2020.csv").write_text("tourney_id\n")
    got = download(tmp_path, years=range(2020, 2022), opener=opener)
    assert got == asked == ["atp_matches_qual_chall_2020.csv", "atp_matches_2021.csv",
                            "atp_matches_qual_chall_2021.csv"]


def test_download_refuses_a_page_that_is_not_a_match_file(tmp_path):
    with pytest.raises(ValueError, match="not a Sackmann match file"):
        download(tmp_path, years=range(2020, 2021), opener=lambda url: _Resp(b"404: Not Found"))
    assert list(tmp_path.iterdir()) == []
