"""As-of serve windows: what is inside a window, and that the live and batch paths agree."""
import numpy as np
import pytest

from tennis.ratings import from_records
from tennis.ratings.serve import (Entry, ServeHistory, match_entries, rolling_windows, shrink,
                                  span_days)
from tennis.ratings.tests.synthetic import generate

D = 20000          # a query date, days since 1970-01-01


def entry(date, level="A", served=80.0):
    return Entry(date, date + span_days(level), served * 0.6, served, 30.0, 80.0)


def history(*entries, strict=True):
    h = ServeHistory(366, strict)
    for e in entries:
        h.add(1, e)
    return h


def test_the_window_is_the_year_before_and_nothing_of_the_day_itself():
    h = history(entry(D - 367), entry(D - 366), entry(D - 30), entry(D), entry(D + 7))
    sk, sn, rk, rn, n = h.window_day(1, D)
    assert n == 2                                   # D-366 in, D-367 out, D and later out
    assert sn == 160.0 and rn == 160.0


def test_an_event_still_running_at_the_tail_is_dropped():
    # a Slam that started 7 days ago runs 14: its matches may be after D
    h = history(entry(D - 30), entry(D - 7, "G"))
    assert h.window_day(1, D)[4] == 1
    assert history(entry(D - 30), entry(D - 7, "G"), strict=False).window_day(1, D)[4] == 2


def test_an_event_that_ended_on_the_day_counts():
    h = history(entry(D - 14, "G"))                 # ends exactly on D
    assert h.window_day(1, D)[4] == 1


def test_a_running_event_behind_a_finished_one_is_kept():
    # Out of a Masters in its first week, then a Challenger that is over by D:
    # he left the Masters before the Challenger began, so its match is in.
    h = history(entry(D - 11, "M"), entry(D - 7, "C"))
    assert h.window_day(1, D)[4] == 2


def test_on_a_shared_date_the_shorter_event_goes_last():
    # Slam qualifying is dated with its main draw: a qualifying loser who then
    # plays a Challenger has both on one date, and the qualifying was over first.
    for order in ((entry(D - 7, "G"), entry(D - 7, "C")), (entry(D - 7, "C"), entry(D - 7, "G"))):
        h = history(*order)
        assert h.window_day(1, D)[4] == 2, "the walk-back must stop at the finished Challenger"


def test_an_empty_window_shrinks_to_the_tour_mean():
    assert shrink(0.0, 0.0, 0.62, 200.0) == 0.62
    assert shrink(80.0, 100.0, 0.62, 200.0) == pytest.approx((80 + 124) / 300)


def test_the_batch_and_the_live_path_agree_on_every_entry():
    m = from_records(generate(years=range(2019, 2022)))
    pids, rows, _, entries = match_entries(m)
    win = rolling_windows(pids, entries, 366, True)
    h = ServeHistory(366, True)
    for p, e in zip(pids, entries):
        h.add(int(p), e)
    for i in range(0, len(entries), 7):
        got = h.window_day(int(pids[i]), entries[i].date)
        assert got[:4] == tuple(win[i, :4]), i


def test_trimming_keeps_everything_a_later_window_can_reach():
    m = from_records(generate(years=range(2019, 2022)))
    pids, _, _, entries = match_entries(m)
    full, trimmed = ServeHistory(366, True), ServeHistory(366, True)
    for p, e in zip(pids, entries):
        full.add(int(p), e)
        trimmed.add(int(p), e)
    last = max(e.date for e in entries)
    trimmed.trim(last - 366)
    assert len(trimmed) < len(full)
    for p in set(pids.tolist()):
        for day in (last, last + 1, last + 100):
            assert trimmed.window_day(p, day) == full.window_day(p, day)


def test_entries_need_both_players_serve_totals():
    rows = generate(years=range(2020, 2021), weeks=2)
    m = from_records(rows)
    pids, rows_, won, entries = match_entries(m)
    complete = np.isfinite(m.w_svpt) & np.isfinite(m.l_svpt)
    assert len(entries) == 2 * int(complete.sum())
    # the winner's return points are the loser's serve points
    for p, r, w, e in zip(pids, rows_, won, entries):
        if w:
            assert (p, e.spw_n, e.rpw_n) == (m.winner_id[r], m.w_svpt[r], m.l_svpt[r])


def test_history_round_trips():
    h = history(entry(D - 30), entry(D - 7, "G"), entry(D - 7, "C"))
    assert ServeHistory.from_dict(h.to_dict()) == h
