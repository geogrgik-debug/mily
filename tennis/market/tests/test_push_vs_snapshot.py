"""Tests for the push-against-snapshot measurement.

The shapes come from the capture host's logs of 22-23.09: a game market's two
outcomes pushed one by one milliseconds apart, a full snapshot of the whole
board on the next score change, and pushes carrying their market_name.
"""
from tennis.ingest.clock import FakeClock
from tennis.ingest.rawlog import RawLog
from tennis.market.push_vs_snapshot import (
    Agreement,
    Lead,
    agreement,
    changes,
    late_first_pushes,
    lead,
    main,
)
from tennis.market.streams import Quote
from tennis.market.tests.test_lead_lag import (  # noqa: F401 -- pb is a fixture
    BOOT,
    GAME_7,
    MID,
    T0,
    full,
    pb,
    push,
    stamp,
)

GAME = ("game", 2, 7, "Исход")


def q(t, outcome, odds, source, *, active=True):
    return Quote(*stamp(t), MID, GAME, outcome, odds, active, source)


def book(t, p1, p2, source):
    """A two-way repricing: the second outcome 4 ms after the first."""
    return [q(t, "П1", p1, source), q(t + 0.004, "П2", p2, source)]


# --------------------------------------------------------------- changes


def test_a_change_is_a_new_bettable_price_within_one_capture():
    quotes = [q(0, "П1", 1.80, "stake"), q(1, "П1", 1.80, "stake", active=False),
              q(2, "П1", 1.85, "stake"), q(3, "П1", 1.85, "stake"),
              q(3.5, "П1", 1.95, "full"),
              None, q(4, "П1", 1.90, "stake"), q(5, "П1", 1.95, "stake")]

    got = changes(quotes, "stake")[(MID, GAME, "П1")]

    assert [(c.ts_received_ns, c.prev, c.odds) for c in got] == [
        (stamp(2)[0], 1.80, 1.85), (stamp(5)[0], 1.90, 1.95)]


def test_an_outcome_pushed_without_a_change_is_still_a_pushed_outcome():
    assert changes([q(0, "П1", 1.80, "stake")], "stake") == {(MID, GAME, "П1"): []}


def test_the_first_push_is_measured_from_the_snapshot_it_was_asked_off():
    quotes = [q(0, "П1", 1.80, "full"), q(3, "П1", 1.85, "stake"), q(4, "П1", 1.85, "full")]

    got = changes(quotes, "stake")[(MID, GAME, "П1")]

    assert [(c.prev, c.odds) for c in got] == [(1.80, 1.85)]
    assert [(c.prev, c.odds) for c in changes(quotes, "full")[(MID, GAME, "П1")]] == [
        (1.80, 1.85)]


# --------------------------------------------------------------- lead


def test_a_pushed_change_pairs_with_the_snapshot_that_shows_its_odds():
    quotes = [q(0, "П1", 1.80, "stake"), q(0.5, "П1", 1.80, "full"),
              q(10, "П1", 1.85, "stake"), q(20, "П1", 1.90, "stake"),
              q(30, "П1", 1.90, "full")]

    got = lead(changes(quotes, "stake"), changes(quotes, "full"))

    assert (got.outcomes, got.pushed, got.snapped) == (1, 2, 1)
    assert got.leads == (10.0,)          # 1.85 no snapshot showed; 1.90 by 10 s


def test_a_snapshot_can_be_first():
    quotes = [q(0, "П1", 1.80, "stake"), q(0.5, "П1", 1.80, "full"),
              q(5, "П1", 1.85, "full"), q(5.4, "П1", 1.85, "stake")]

    got = lead(changes(quotes, "stake"), changes(quotes, "full"))

    assert [round(x, 3) for x in got.leads] == [-0.4]


def test_changes_further_apart_than_the_window_do_not_pair():
    quotes = [q(0, "П1", 1.80, "stake"), q(0.5, "П1", 1.80, "full"),
              q(10, "П1", 1.85, "stake"), q(200, "П1", 1.85, "full")]

    assert lead(changes(quotes, "stake"), changes(quotes, "full"), window_s=100).leads == ()
    assert lead(changes(quotes, "stake"), changes(quotes, "full"), window_s=300).leads == (190.0,)


def test_an_outcome_nobody_pushed_is_left_out():
    quotes = [q(0, "П1", 1.80, "full"), q(5, "П1", 1.85, "full")]
    got = lead(changes(quotes, "stake"), changes(quotes, "full"))
    assert (got.outcomes, got.snapped, got.leads) == (0, 0, ())


def test_a_change_does_not_pair_across_a_break():
    """The snapshot of a change pushed just before a reconnect is lost; a
    later snapshot at the same odds after it would time the reconnect."""
    quotes = [q(0, "П1", 1.80, "full"), q(1, "П1", 1.85, "stake"), None,
              q(2, "П1", 1.80, "full"), q(3, "П1", 1.85, "full")]

    got = lead(changes(quotes, "stake"), changes(quotes, "full"))

    assert (got.pushed, got.snapped, got.leads) == (1, 1, ())


def test_a_tie_is_neither_first():
    got = Lead(outcomes=1, pushed=3, snapped=3, leads=(0.5, 0.0, -0.2))
    assert got.order == (1, 1, 1)


def test_changes_pair_by_odds_not_by_the_nearest_time():
    """The snapshot shows 1.85 at 11.9 s, a tenth of a second before the push
    of the next price, 1.90: the pair is 1.85 with 1.85, 1.9 s apart."""
    quotes = [q(0, "П1", 1.80, "full"), q(10, "П1", 1.85, "stake"),
              q(11.9, "П1", 1.85, "full"), q(12, "П1", 1.90, "stake")]

    got = lead(changes(quotes, "stake"), changes(quotes, "full"))

    assert [round(x, 3) for x in got.leads] == [1.9]


def test_a_first_push_a_snapshot_beat_is_counted_apart():
    """Found in review: the snapshot moved the price at 10 s and the outcome's
    first push came at 13 s. Measured from the price last quoted, the push is
    no change, so nothing pairs and the snapshot's lead would go unseen."""
    quotes = [q(0, "П1", 1.80, "full"), q(10, "П1", 1.85, "full"), q(13, "П1", 1.85, "stake")]

    assert late_first_pushes(quotes) == (3.0,)
    assert lead(changes(quotes, "stake"), changes(quotes, "full")).leads == ()


def test_a_first_push_ahead_of_the_snapshot_is_the_push_first():
    quotes = [q(0, "П1", 1.80, "full"), q(10, "П1", 1.85, "stake"), q(13, "П1", 1.85, "full")]

    assert late_first_pushes(quotes) == ()
    assert lead(changes(quotes, "stake"), changes(quotes, "full")).leads == (3.0,)


def test_only_the_first_push_of_an_outcome_is_held_against_the_snapshot():
    """Found in review: without the first-push rule the capture host's count
    went from 38 to 57 and its longest from 14.89 s to 236.51 s. Here the
    later push, at the price the later snapshot moved to, is a change of the
    pushes' own and pairs."""
    quotes = [q(0, "П1", 1.80, "full"), q(5, "П1", 1.85, "stake"),
              q(8, "П1", 1.90, "full"), q(13, "П1", 1.90, "stake")]
    assert late_first_pushes(quotes) == ()


def test_a_first_push_repeated_is_counted_once():
    quotes = [q(0, "П1", 1.80, "full"), q(10, "П1", 1.85, "full"),
              q(13, "П1", 1.85, "stake"), q(20, "П1", 1.85, "stake")]
    assert late_first_pushes(quotes) == (3.0,)


def test_a_first_push_repeating_the_board_is_not_late():
    """A first push at the price the snapshot started from moved nothing."""
    assert late_first_pushes([q(0, "П1", 1.80, "full"), q(1, "П1", 1.80, "stake")]) == ()


def test_a_break_forgets_what_the_snapshot_moved():
    quotes = [q(0, "П1", 1.80, "full"), q(10, "П1", 1.85, "full"), None,
              q(13, "П1", 1.85, "stake")]
    assert late_first_pushes(quotes) == ()


# --------------------------------------------------------------- rollbacks


def test_a_snapshot_behind_the_pushes_is_a_rollback():
    """Pushes move the book twice; the snapshot after them still shows the
    first move. Differenced against the snapshot before it, it is news."""
    quotes = [*book(0, 2.30, 1.47, "full"),
              *book(3, 2.35, 1.45, "stake"),
              *book(5, 2.40, 1.42, "stake"),
              *book(8, 2.35, 1.45, "full"),
              *book(12, 2.40, 1.42, "full")]

    assert agreement(quotes) == Agreement(same=2, older=2, gone=0, other=0)


def test_a_snapshot_ahead_of_the_pushes_is_not_a_rollback():
    quotes = [*book(3, 2.35, 1.45, "stake"), *book(8, 2.50, 1.38, "full")]
    assert agreement(quotes) == Agreement(same=0, older=0, gone=0, other=2)


def test_only_outcomes_already_pushed_are_held_against_the_pushes():
    quotes = [*book(0, 2.30, 1.47, "full"), q(3, "П1", 2.35, "stake"),
              *book(8, 2.35, 1.45, "full")]
    assert agreement(quotes) == Agreement(same=1, older=0, gone=0, other=0)


def test_suspension_is_part_of_the_price():
    quotes = [q(0, "П1", 2.35, "stake"), q(1, "П1", 2.35, "stake", active=False),
              q(2, "П1", 2.35, "full")]
    assert agreement(quotes) == Agreement(same=0, older=1, gone=0, other=0)


def test_a_snapshot_taking_an_outcome_off_the_board_first_is_no_rollback():
    """On the capture host the snapshot took a pushed outcome off the board
    12,252 times before any push said so."""
    quotes = [q(0, "П1", 2.35, "stake"), q(5, "П1", None, "full", active=False)]
    assert agreement(quotes) == Agreement(same=0, older=0, gone=1, other=0)


def test_a_break_forgets_the_pushes():
    quotes = [q(0, "П1", 2.35, "stake"), q(1, "П1", 2.40, "stake"), None,
              q(2, "П1", 2.35, "full")]
    assert agreement(quotes).total == 0


# --------------------------------------------------------------- command line


def write(root, rows):
    clock = FakeClock(wall_ns=T0, mono_ns=T0 - BOOT)
    now = 0.0
    with RawLog(root, provider="betboom", clock=clock) as log:
        for t, frame in rows:
            clock.advance(t - now)
            now = t
            log.write(frame["payload"], channel="tree_ws")
    return root


def test_the_command_line_on_a_capture_with_pushes(pb, tmp_path, capsys):
    two = lambda p1, p2: [(GAME_7, "П1", p1), (GAME_7, "П2", p2)]  # noqa: E731
    frames = [(0, full(pb, 0, two(2.30, 1.47))),
              (3, push(pb, 3, "П1", 2.35)), (3.004, push(pb, 3.004, "П2", 1.45)),
              (5, push(pb, 5, "П1", 2.40)), (5.004, push(pb, 5.004, "П2", 1.42)),
              (8, full(pb, 8, two(2.35, 1.45))),
              (12, full(pb, 12, two(2.40, 1.42)))]
    write(tmp_path, frames)

    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 outcomes pushed" in out
    assert ("4: at the latest pushed price 2, at a price the pushes had left 2 "
            "(each a rollback), off the board before a push said so 0, "
            "at a price no push had shown 0") in out
    assert "4 changes by push, 4 by snapshot; 4 paired" in out
    assert "push first 4 (100.0%)" in out
    assert "first push at a price a snapshot had already moved to: 0" in out


def test_the_command_line_refuses_two_captures_at_once(pb, tmp_path, capsys):
    """Two recorders writing into one folder would count every change twice."""
    two = [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)]
    frames = [(0, full(pb, 0, two)), (3, push(pb, 3, "П1", 2.35)), (12, full(pb, 12, two))]
    write(tmp_path, frames)
    write(tmp_path, frames)

    assert main([str(tmp_path)]) == 2
    assert "at the same time" in capsys.readouterr().err


def test_the_command_line_refuses_a_capture_without_pushes(pb, tmp_path, capsys):
    two = [(GAME_7, "П1", 2.30), (GAME_7, "П2", 1.47)]
    write(tmp_path, [(0, full(pb, 0, two)), (12, full(pb, 12, two))])

    assert main([str(tmp_path)]) == 2
    assert "no pushes" in capsys.readouterr().err
