"""Difference two consecutive snapshots of one match's board.

BetBoom does not push a price change on its own. It re-sends every market on a
match in one `newsletters_full_match`, and a capture can currently say only
that such a snapshot arrived -- not which prices moved inside it. This module
answers that.

The answer is not "almost nothing". Measured on the 2026-09-22 capture, 1,577
consecutive pairs across 20 matches: a snapshot carries a median of 126
outcomes and 69 of them are repriced, so roughly half the board moves every
12 seconds. Only 4.3% of pairs are identical books. Outcomes also come and go
as a game starts and ends -- a median of 0 per pair but up to 118 at once.

Identity across a pair
----------------------
An outcome is `(parse_market(market_name), name)`. That this is a *stable
identity* and not merely a convenient label was checked on the same capture,
because a diff keyed on something unstable would report invented moves:

* it never collided inside a snapshot -- 176,995 stakes, zero duplicate keys;
* the provider's own `stake_id` never changed under it;
* a handicap's `argument` never moved under it, so "Фора по геймам / Ф1" never
  silently became a different line between two snapshots.

So `period_name` is not needed to tell two outcomes apart. Where it carries a
game address at all (1,178 of 37,675 game stakes) it only repeats what
`market_name` already says; it never carries one that `market_name` lacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tennis.market.names import MarketRef, parse_market

__all__ = ["PriceChange", "diff_snapshots"]


@dataclass(frozen=True)
class PriceChange:
    """One outcome that is not what it was in the previous snapshot.

    `old` and `new` are the quoted decimal odds, or None when the outcome was
    absent from that side of the pair. An absent outcome is also reported as
    not active, since it cannot be bet; `new is None` is what separates "gone
    from the board" from "still quoted but suspended".

    Price and activity are reported side by side rather than folded together,
    because a suspension is usually not a price move: across 2,472
    active->inactive transitions in the 2026-09-22 capture the factor was
    carried over unchanged 92.2% of the time. The converse matters more --
    the odd values that sit on an inactive stake (1.01, 100.0, and 0.0) are
    placeholders on a dead market, not quotes anyone could take, so `new`
    means nothing unless `is_active`.
    """

    ref: MarketRef
    outcome: str
    old: float | None
    new: float | None
    was_active: bool
    is_active: bool

    @property
    def repriced(self) -> bool:
        """The odds moved between two quotes that both existed."""
        return self.old is not None and self.new is not None and self.old != self.new

    @property
    def suspended(self) -> bool:
        """Was bettable, is still on the board, is no longer bettable."""
        return self.was_active and not self.is_active and self.new is not None


def _sort_key(change: PriceChange) -> tuple:
    """Market address then outcome. `set_no`/`game_no` are None off a game."""
    ref = change.ref
    return (ref.scope, ref.set_no or 0, ref.game_no or 0, ref.kind, change.outcome)


def _index(stakes: Iterable) -> tuple[dict, set[int]]:
    """{(MarketRef, outcome name): stake} for one snapshot, and its match ids.

    A repeated key keeps the first stake, as `measure._books` does. It has
    never been observed, and a snapshot that started doing it is not worth
    crashing a recorder over.
    """
    book: dict[tuple[MarketRef, str], object] = {}
    match_ids: set[int] = set()
    for stake in stakes:
        book.setdefault((parse_market(stake.market_name), stake.name), stake)
        match_id = getattr(stake, "match_id", 0)
        if match_id:
            match_ids.add(match_id)
    return book, match_ids


def diff_snapshots(before: Iterable, after: Iterable) -> list[PriceChange]:
    """Every outcome that differs between two consecutive snapshots of a match.

    Each argument is the `stakes` of one snapshot -- a `newsletters_full_match`
    or the `matches_subscribe_full` that opens a subscription. Anything with
    `market_name`, `name`, `factor` and `is_active` will do, which is what lets
    this be tested without a protobuf.

    Outcomes that are present and identical on both sides are left out; that is
    the whole point. `factor` is compared exactly, because the feed quotes in
    discrete ticks and a tolerance would only hide the smallest real moves.

    The result is ordered by market address then outcome name, so two runs over
    one pair read the same way.

    Raises ValueError if the two snapshots are of different matches. The feed
    interleaves twenty of them, so holding a single `last_snapshot` instead of
    one per match is an easy mistake, and it would produce a full-board diff
    that looks entirely plausible.
    """
    old, old_ids = _index(before)
    new, new_ids = _index(after)
    seen_matches = old_ids | new_ids
    if len(seen_matches) > 1:
        raise ValueError("snapshots are of different matches: "
                         f"{sorted(seen_matches)}")

    changes = []
    for key in old.keys() | new.keys():
        ref, outcome = key
        a, b = old.get(key), new.get(key)
        old_price = a.factor if a is not None else None
        new_price = b.factor if b is not None else None
        was_active = a is not None and bool(a.is_active)
        is_active = b is not None and bool(b.is_active)
        if (a is not None and b is not None
                and old_price == new_price and was_active == is_active):
            continue
        changes.append(PriceChange(ref=ref, outcome=outcome,
                                   old=old_price, new=new_price,
                                   was_active=was_active, is_active=is_active))
    changes.sort(key=_sort_key)
    return changes
