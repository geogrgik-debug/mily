"""Which match of one book is which match of another, by who plays.

Two books number matches their own way and name players their own way:
BetBoom writes "Тимофеева М." (surname, initials) and 1win "Мария Тимофеева"
(given name, surname), both in Russian. `tennis.ingest.names.match_key` folds
names to ASCII and so drops Cyrillic whole ("Медведев Д." gives ""), which
leaves the Russian words themselves to go by.

Two names are one player when a word of one is a word of the other -- or,
for words of six letters and more, a spelling apart ("Риналдо"/"Ринальдо",
"Вискандт"/"Висканд"). Initials and words under three letters say too little
and are dropped. Two matches are one when both players are, one to one, and
the two books quoted them at overlapping times. Then each outcome is named by
player: 1win's "1" becomes BetBoom's "П1" or "П2", whichever side that player
is on there -- home and away need not agree between books.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Sequence

from tennis.market.streams import PriceEvent, Stream

__all__ = ["SLACK_S", "name_words", "same_player", "pair_matches", "rekey", "align_books"]

# How far apart two books' quotes of one match may begin or end: one recorder
# may be subscribed later or dropped earlier than the other.
SLACK_S = 600.0
_SPLIT = re.compile(r"[\s\-]+")


def name_words(name: str) -> frozenset[str]:
    """The words of a player's name that can tell them apart: no initials
    ("Дж.", "Ц.Х."), nothing under three letters, "ё" read as "е"."""
    words = _SPLIT.split(name.lower().replace("ё", "е"))
    return frozenset(w.strip(".,()'") for w in words
                     if not w.endswith(".") and len(w.strip(".,()'")) >= 3)


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 6 and SequenceMatcher(None, a, b).ratio() >= 0.85


def same_player(a: str, b: str) -> bool:
    return any(_same_word(x, y) for x in name_words(a) for y in name_words(b))


def _spans(events: Sequence[PriceEvent]) -> dict:
    spans: dict = {}
    for e in events:
        lo, hi = spans.get(e.match, (e.ts_received_ns, e.ts_received_ns))
        spans[e.match] = (min(lo, e.ts_received_ns), max(hi, e.ts_received_ns))
    return spans


def _sides(pair: tuple[str, str], onto: tuple[str, str]) -> dict | None:
    """Outcome "1"/"2" of `pair` to "П1"/"П2" of `onto`, if the players say so
    one way and not the other."""
    straight = same_player(pair[0], onto[0]) and same_player(pair[1], onto[1])
    crossed = same_player(pair[0], onto[1]) and same_player(pair[1], onto[0])
    if straight == crossed:
        return None
    return {"1": "П1", "2": "П2"} if straight else {"1": "П2", "2": "П1"}


def pair_matches(onto: Stream, other: Stream, slack_s: float = SLACK_S) -> tuple[dict, Counter]:
    """`other`'s match id -> (`onto`'s match id, outcome map), for every match
    of `other` that exactly one match of `onto` has the same players and time."""
    slack = int(slack_s * 1e9)
    onto_spans, other_spans = _spans(onto.events), _spans(other.events)
    paired: dict = {}
    counts: Counter = Counter()
    for mid, (lo, hi) in other_spans.items():
        pair = other.players.get(mid)
        found = []
        for omid, (olo, ohi) in onto_spans.items():
            names = onto.players.get(omid)
            if pair is None or names is None or lo > ohi + slack or olo > hi + slack:
                continue
            sides = _sides(pair, names)
            if sides is not None:
                found.append((omid, sides))
        if len(found) == 1:
            paired[mid] = found[0]
            counts["paired"] += 1
        else:
            counts["ambiguous" if found else "unpaired"] += 1
    # One to one the other way too: a match of `onto` claimed twice is trusted
    # for neither -- two books cannot have played it twice.
    claims = Counter(omid for omid, _ in paired.values())
    shared = {mid for mid, (omid, _) in paired.items() if claims[omid] > 1}
    counts["paired"] -= len(shared)
    counts["ambiguous"] += len(shared)
    return {mid: pair for mid, pair in paired.items() if mid not in shared}, counts


def rekey(other: Stream, onto: Stream) -> tuple[Stream, str]:
    """`other`'s events under `onto`'s match ids and outcome names; the
    matches that did not pair are left out, and the note says how many."""
    paired, counts = pair_matches(onto, other)
    events = [replace(e, match=paired[e.match][0], outcome=paired[e.match][1][e.outcome])
              for e in other.events
              if e.match in paired and e.outcome in paired[e.match][1]]
    players = {paired[m][0]: other.players[m] for m in paired}
    total = len({e.match for e in other.events})
    note = (f"{other.label}: {len(paired)} of {total} matches paired with {onto.label}'s "
            f"by the players' names")
    if counts["ambiguous"]:
        note += f" ({counts['ambiguous']} ambiguous, left out)"
    return replace(other, events=events, players=players), note


def align_books(streams: Sequence[Stream]) -> tuple[list[Stream], list[str]]:
    """Every stream of another book, re-keyed onto the first stream's matches.
    Streams of the first stream's own book are left as they are."""
    first = streams[0]
    out, notes = [first], []
    for s in streams[1:]:
        if s.provider == first.provider:
            out.append(s)
            continue
        if not s.players or not first.players:
            notes.append(f"{s.label}: no players' names to pair its matches with "
                         f"{first.label}'s, compared as keyed")
            out.append(s)
            continue
        rekeyed, note = rekey(s, first)
        out.append(rekeyed)
        notes.append(note)
    return out, notes
