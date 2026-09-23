"""Reading the two point-by-point sources, tying matches to Sackmann, pricing priors.

The pins that matter most: a game is kept only if it ends exactly on its last
point; the server of every point is the one the scoring rules say; the Slam
draw comes from the match number; a name like 'N. Djokovic' finds 'Novak
Djokovic'; and the streamed priors equal a snapshot rebuilt as of each date.
"""

import csv

import numpy as np

from tennis.eval.join import Joined, join_pbp, join_slam, price, stream_priors
from tennis.eval.pbp import match_from_row, parse_pbp, score_sets
from tennis.eval.points import Game, MatchPoints, alternates, norm_name, same_player, valid_game
from tennis.eval.slam import load_slam
from tennis.ratings.sackmann import from_records
from tennis.ratings.snapshot import RatingsSnapshot
from tennis.ratings.tests.synthetic import generate


# ---- games -------------------------------------------------------------------

def _reg(server, won):
    return Game(server, tuple((server, w) for w in won))


def test_a_game_must_end_exactly_on_its_last_point():
    assert valid_game(_reg(1, [1, 1, 1, 1]))
    assert valid_game(_reg(1, [1, 1, 0, 0, 1, 0, 0, 0]))          # broken after deuce
    assert not valid_game(_reg(1, [1, 1, 1]))                      # not over
    assert not valid_game(_reg(1, [1, 1, 1, 1, 0]))                # a point after the end
    assert not valid_game(Game(1, ((1, 1), (2, 1), (1, 1), (1, 1))))   # two servers, not a tie-break


def test_a_tie_break_is_decided_at_seven_or_ten_by_two():
    seven_love = [(1, 1), (2, 0), (2, 0), (1, 1), (1, 1), (2, 0), (2, 0)]
    assert valid_game(Game(1, tuple(seven_love), tiebreak=True))
    assert not valid_game(Game(1, tuple(seven_love[:6]), tiebreak=True))
    assert Game(1, tuple(seven_love), tiebreak=True).winner() == 1


def test_serve_must_alternate_game_by_game():
    g1, g2 = _reg(1, [1, 1, 1, 1]), _reg(2, [1, 1, 1, 1])
    assert alternates([g1, g2, g1])
    assert not alternates([g1, g1])


def test_names_agree_across_the_sources_spellings():
    assert norm_name("Novak Djokovic") == norm_name("N. Djokovic") == norm_name("N Djokovic")
    assert norm_name("Jo-Wilfried Tsonga") == norm_name("J. Tsonga")
    assert same_player("Victor Estrella Burgos", "Victor Estrella")
    assert same_player("Albert Ramos-Vinolas", "Albert Ramos")
    assert same_player("Alex Jr. Bogomolov", "Alex Bogomolov Jr")
    assert not same_player("Alexander Zverev", "Mischa Zverev")
    assert not same_player("Andy Murray", "Jamie Murray")


# ---- tennis_pointbypoint -----------------------------------------------------

def _pbp_match():
    """P1 wins 7-6 6-0: twelve holds, a 7-0 tie-break he serves first, then six
    straight games, three of them breaks."""
    set1 = ";".join(["SSSS"] * 12) + ";" + "S/RR/SS/RR"
    set2 = ";".join(["RRRR", "SSSS"] * 3)       # P2 serves first in set 2 and is broken
    return set1 + "." + set2


def test_pbp_servers_alternate_and_the_tie_break_changes_serve_every_two_points():
    games, sets = parse_pbp(_pbp_match())
    assert [g.server for g in games[:13]] == [1, 2] * 6 + [1]
    tb = games[12]
    assert tb.tiebreak and [s for s, _ in tb.points] == [1, 2, 2, 1, 1, 2, 2]
    assert games[13].server == 2               # the tie-break's receiver opens set 2
    assert sets == [(7, 6), (6, 0)]


def test_aces_and_double_faults_count_for_the_server_and_returner():
    games, _ = parse_pbp("SASA;DRRD")
    assert games[0].points == ((1, 1),) * 4
    assert games[1].points == ((2, 0),) * 4


def test_the_score_column_is_read_from_the_winners_side():
    assert score_sets("7-6(5) 4-6 6-3") == [(7, 6), (4, 6), (6, 3)]
    assert score_sets("6-4 RET") is None


def _row(**kw):
    row = {"pbp_id": "1", "date": "28 Jul 11", "tny_name": "T", "tour": "ATP", "draw": "Main",
           "server1": "Ann A", "server2": "Bob B", "winner": "1", "pbp": _pbp_match(),
           "score": "7-6(0) 6-0"}
    row.update(kw)
    return row


def test_a_pbp_row_becomes_a_match_only_if_the_score_agrees():
    m, why = match_from_row(_row())
    assert why == "ok" and m.date == "2011-07-28" and m.level == "tour" and len(m.games) == 19
    assert match_from_row(_row(score="6-4 6-0"))[1] == "score_mismatch"
    # the mirrored score fits the sets, but the points say player 1 won
    assert match_from_row(_row(score="6-7(0) 0-6", winner="2"))[1] == "winner_mismatch"
    assert match_from_row(_row(pbp=_pbp_match().replace("SSSS", "SSS", 1)))[1] == "bad_game"
    assert match_from_row(_row(tour="FU"))[1] == "level"


# ---- Grand Slam point by point -----------------------------------------------

FIELDS = ["match_id", "SetNo", "GameNo", "PointNumber", "PointWinner", "PointServer",
          "P1Score", "P2Score", "GameWinner"]


def _slam_rows(mid, games):
    """Rows for `games` = [(server, [server won?...]), ...], with a marker row first."""
    rows = [dict(match_id=mid, SetNo="1", GameNo="1", PointNumber="0X", PointWinner="0",
                 PointServer="0", P1Score="0", P2Score="0", GameWinner="0")]
    n = 0
    for gno, (server, won) in enumerate(games, start=1):
        for i, w in enumerate(won):
            n += 1
            winner = server if w else 3 - server
            last = i == len(won) - 1
            rows.append(dict(match_id=mid, SetNo="1", GameNo=str(gno), PointNumber=str(n),
                             PointWinner=str(winner), PointServer=str(server),
                             P1Score="GAME" if last else "15", P2Score="0",
                             GameWinner=str(winner) if last else "0"))
    return rows


def _write_slam(tmp_path, matches):
    with open(tmp_path / "2019-wimbledon-points.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for mid, games in matches:
            w.writerows(_slam_rows(mid, games))
    with open(tmp_path / "2019-wimbledon-matches.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["match_id", "player1", "player2"])
        w.writeheader()
        for mid, _ in matches:
            w.writerow({"match_id": mid, "player1": "N. Djokovic", "player2": "R. Federer"})


def test_slam_reader_keeps_the_mens_draw_and_drops_broken_matches(tmp_path):
    good = [(1, [1, 1, 1, 1]), (2, [0, 0, 0, 0]), (1, [1, 0, 1, 1, 1])]
    _write_slam(tmp_path, [
        ("2019-wimbledon-1101", good),
        ("2019-wimbledon-2101", good),                          # women's draw
        ("2019-wimbledon-1102", [(1, [1, 1, 1]), (2, [1, 1, 1, 1])]),   # a point missing
        ("2019-wimbledon-1103", [(1, [1, 1, 1, 1]), (1, [1, 1, 1, 1])]),  # serve repeats
    ])
    matches, why = load_slam(tmp_path)
    assert [m.key for m in matches] == ["2019-wimbledon-1101"]
    assert why == {"ok": 1, "incomplete_game": 1, "serve_order": 1}
    m = matches[0]
    assert m.event == "2019-wimbledon" and m.name1 == "N. Djokovic"
    assert [g.server for g in m.games] == [1, 2, 1]
    assert m.games[1].points == ((2, 0),) * 4
    assert not any(g.tiebreak for g in m.games)      # 'GAME' on a last point is not a tie-break


# ---- joining to Sackmann and pricing ------------------------------------------

def _mp(name1, name2, *, source="pbp", level="tour", date="2020-01-08", event="E"):
    return MatchPoints(key=f"{name1}-{name2}-{date}", source=source, level=level,
                       year=int(date[:4]) if date else 2019, date=date, event=event,
                       name1=name1, name2=name2, games=())


def _sack(rows):
    base = {"tourney_level": "A", "surface": "Hard", "best_of": "3", "match_num": "1",
            "src": "tour"}
    return from_records([{**base, **r} for r in rows])


def test_pbp_join_finds_the_row_by_names_and_date_and_knows_the_sides():
    sack = _sack([
        dict(tourney_id="2020-1", tourney_date="20200106", winner_id="1", loser_id="2",
             winner_name="Novak Djokovic", loser_name="Roger Federer"),
        dict(tourney_id="2020-2", tourney_date="20200106", winner_id="3", loser_id="4",
             winner_name="Albert Ramos", loser_name="Victor Estrella"),
    ])
    joined, why = join_pbp([_mp("R. Federer", "N. Djokovic"),
                            _mp("Victor Estrella Burgos", "Albert Ramos-Vinolas"),
                            _mp("R. Federer", "N. Djokovic", date="2020-03-01")], sack)
    assert why == {"ok": 1, "ok_loose": 1, "no_row": 1}
    assert [(j.row, j.p1_is_winner) for j in joined] == [(0, False), (1, False)]


def test_pbp_join_takes_the_event_that_started_last():
    sack = _sack([
        dict(tourney_id="2020-1", tourney_date="20200106", winner_id="1", loser_id="2",
             winner_name="Novak Djokovic", loser_name="Roger Federer", match_num="1"),
        dict(tourney_id="2020-2", tourney_date="20200113", winner_id="2", loser_id="1",
             winner_name="Roger Federer", loser_name="Novak Djokovic", match_num="2"),
    ])
    joined, _ = join_pbp([_mp("Novak Djokovic", "Roger Federer", date="2020-01-16")], sack)
    assert [(j.row, j.p1_is_winner) for j in joined] == [(1, False)]


def test_a_challenger_match_never_joins_a_tour_row():
    sack = _sack([dict(tourney_id="2020-1", tourney_date="20200106", winner_id="1",
                       loser_id="2", winner_name="Ann Aa", loser_name="Bob Bb")])
    _, why = join_pbp([_mp("Ann Aa", "Bob Bb", level="chall")], sack)
    assert why == {"no_row": 1}


def test_slam_join_looks_only_inside_the_event():
    sack = _sack([
        dict(tourney_id="2019-540", tourney_date="20190701", tourney_level="G", best_of="5",
             winner_id="1", loser_id="2", winner_name="Novak Djokovic",
             loser_name="Roger Federer"),
        dict(tourney_id="2019-560", tourney_date="20190826", tourney_level="G", best_of="5",
             winner_id="2", loser_id="1", winner_name="Roger Federer",
             loser_name="Novak Djokovic"),
    ])
    wimbledon = _mp("N. Djokovic", "R. Federer", source="slam", level="slam", date="",
                    event="2019-wimbledon")
    joined, why = join_slam([wimbledon], sack)
    assert why == {"ok": 1} and [(j.row, j.p1_is_winner) for j in joined] == [(0, True)]


def test_streamed_priors_equal_a_snapshot_rebuilt_as_of_each_date():
    sack = from_records(generate(years=range(2019, 2022)))
    days = sack.date.astype("datetime64[D]")
    rows = [int(r) for r in np.linspace(len(sack) // 3, len(sack) - 1, 12).astype(int)]
    got = stream_priors(sack, rows)
    for r in rows:
        snap = RatingsSnapshot.build(sack, as_of=str(days[r]))
        want = snap.prior(int(sack.winner_id[r]), int(sack.loser_id[r]), sack.surface[r],
                          int(sack.best_of[r]), as_of=str(days[r]))
        assert got[r] == want


def test_price_puts_player_one_first():
    sack = from_records(generate(years=range(2019, 2021)))
    r = len(sack) - 1
    pri = stream_priors(sack, [r])
    m = _mp(sack.loser_name[r], sack.winner_name[r])
    pm = price([Joined(m, r, False)], sack, pri)[0]
    assert (pm.p1, pm.p2) == (pri[r].p_serve_b, pri[r].p_serve_a)
    assert pm.best_of == int(sack.best_of[r]) and pm.surface == sack.surface[r]
