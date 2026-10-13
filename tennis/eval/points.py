"""A match as the live state meets it: games in order, each point with its server.

Both historical sources -- Grand Slam point by point and `tennis_pointbypoint` --
are turned into this one shape, so everything downstream (priors, the live
state, the evaluation) is written once. A point is (server, won): who served
it, 1 or 2 for the match's player 1 or 2, and 1 if that server won it. A game
is either a regular service game, every point served by one player, or a
tie-break, where the serve changes.

Validation is strict on purpose: a game that does not end exactly on its last
point means the source lost or invented a point, and the belief built from it
would be wrong in a way no test could see later. Such a match is dropped and
counted, never repaired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

Point = Tuple[int, int]          # (server 1|2, 1 if the server won the point)


@dataclass(frozen=True)
class Game:
    server: int                  # 1 or 2; for a tie-break, who served its first point
    points: Tuple[Point, ...]
    tiebreak: bool = False

    def winner(self) -> int:
        """1 or 2: who won the game, from its points."""
        p1 = sum(won if srv == 1 else 1 - won for srv, won in self.points)
        return 1 if 2 * p1 > len(self.points) else 2


@dataclass(frozen=True)
class MatchPoints:
    key: str                     # the source's match id
    source: str                  # 'slam' or 'pbp'
    level: str                   # 'slam', 'tour' or 'chall'
    year: int
    date: str                    # ISO match date (pbp); empty for slam, whose join supplies it
    event: str                   # slam: '<year>-<slam>'; pbp: the source's tournament name
    name1: str
    name2: str
    games: Tuple[Game, ...]


def decided_at(p1_won: Sequence[int], target: int) -> Optional[int]:
    """Index of the point that decided a game or tie-break -- first to `target`
    with a margin of two -- or None if it never was decided."""
    a = b = 0
    for i, w in enumerate(p1_won):
        if w:
            a += 1
        else:
            b += 1
        if max(a, b) >= target and abs(a - b) >= 2:
            return i
    return None


def valid_game(game: Game) -> bool:
    """A regular game served by one player, or a tie-break to 7 (10 for a
    deciding match tie-break), that ends exactly on its last point."""
    if not game.points:
        return False
    p1 = [won if srv == 1 else 1 - won for srv, won in game.points]
    if not game.tiebreak:
        if any(srv != game.server for srv, _ in game.points):
            return False
        return decided_at(p1, 4) == len(p1) - 1
    return any(decided_at(p1, t) == len(p1) - 1 for t in (7, 10))


def alternates(games: Sequence[Game]) -> bool:
    """Serve alternates game by game; a tie-break counts as a game served by
    whoever served its first point. A gap in a source breaks this."""
    return all(g.server != h.server for g, h in zip(games, games[1:]))


def _tokens(name: str) -> list:
    return re.sub(r"[^a-z ]", " ", str(name).strip().lower()).split()


def norm_name(name: str) -> str:
    """First initial plus surname: 'Novak Djokovic', 'N. Djokovic' and
    'N Djokovic' agree. AO and RG abbreviate given names in 2018-2021, and
    matching full strings dropped those events (START_HERE, section 10)."""
    parts = _tokens(name)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0][0] + " " + parts[-1]


# Not a surname on their own: 'Alex Bogomolov Jr.', 'J. Del Potro'.
_PARTICLES = {"jr", "sr", "ii", "iii", "iv", "de", "del", "della", "da", "di", "dos",
              "das", "du", "van", "von", "der", "den", "la", "le", "el"}


def name_key(name: str) -> Tuple[str, frozenset]:
    """(first initial, surname words): 'Albert Ramos-Vinolas' -> ('a', {'ramos', 'vinolas'})."""
    parts = _tokens(name)
    if len(parts) < 2:
        return "", frozenset(parts)
    rest = [t for t in parts[1:] if len(t) > 1 and t not in _PARTICLES]
    return parts[0][0], frozenset(rest or parts[1:])


def same_player(a: str, b: str) -> bool:
    """The looser test, for when `norm_name` finds nothing: the same initial and
    a surname word in common -- 'Victor Estrella Burgos' is Sackmann's 'Victor
    Estrella', 'Albert Ramos Vinolas' his 'Albert Ramos'."""
    ia, sa = name_key(a)
    ib, sb = name_key(b)
    return bool(ia) and ia == ib and bool(sa & sb)
