"""Parse BetBoom market and outcome display strings into references.

Everything here is derived from strings actually seen on the live feed, not
from a published spec: there is none. The shapes below were extracted from
captured logs (170 distinct market names, 90 of them mentioning a game).

The one structural fact that matters: a game market carries its address in
`market_name` and leaves `period_name` empty. So "2-й сет 6-й гейм: Исход" is
the only thing that says which game the price is for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "MarketRef",
    "OUTCOME_POINTS",
    "parse_market",
    "parse_exact_score_outcome",
    "parse_server_score_outcome",
    "GAME_WINNER",
    "GAME_EXACT_SCORE",
    "GAME_SERVER_SCORE_OR_BREAK",
]

# Market kinds this project cares about, by their tail after the address.
GAME_WINNER = "Исход"
GAME_EXACT_SCORE = "Точный счёт"
GAME_SERVER_SCORE_OR_BREAK = "Точный счёт подающего или брейк"

_GAME = re.compile(r"^(\d+)-й сет (\d+)-й гейм:\s*(.+)$")
_SET = re.compile(r"^(\d+)-й сет:\s*(.+)$")

# The loser's game score, as the book spells it, to points won.
OUTCOME_POINTS = {"0": 0, "15": 1, "30": 2, "40": 3}

_EXACT_HOME = re.compile(r"^П1:(0|15|30|40)$")
_EXACT_AWAY = re.compile(r"^(0|15|30|40):П2$")
_SERVER_SCORE = re.compile(r"^П:(0|15|30|40)$")
BREAK_OUTCOME = "Брейк"


@dataclass(frozen=True)
class MarketRef:
    """Where a market sits: whole match, a set, or one game of one set.

    `kind` is the tail after the address, kept as the provider spells it. It is
    not translated or enumerated, because the catalogue is the server's and
    changes without notice; code should compare against the constants above and
    treat anything else as a market it does not model yet.
    """

    scope: str                  # 'match' | 'set' | 'game'
    kind: str
    set_no: int | None = None
    game_no: int | None = None

    @property
    def is_game(self) -> bool:
        return self.scope == "game"

    def key(self) -> tuple:
        """Identity of the market for grouping prices that belong together."""
        return (self.scope, self.set_no, self.game_no, self.kind)


def parse_market(market_name: str) -> MarketRef:
    """Structure a market_name. Never raises: an unknown shape is match scope.

    Falling back rather than raising is deliberate. The catalogue is large and
    the server can add to it at any time; a recorder that crashed on an
    unrecognised market would lose the capture, which is the one thing the
    whole pipeline is built to prevent.
    """
    name = (market_name or "").strip()
    m = _GAME.match(name)
    if m:
        return MarketRef(scope="game", set_no=int(m.group(1)),
                         game_no=int(m.group(2)), kind=m.group(3).strip())
    m = _SET.match(name)
    if m:
        return MarketRef(scope="set", set_no=int(m.group(1)),
                         kind=m.group(2).strip())
    return MarketRef(scope="match", kind=name)


def parse_exact_score_outcome(outcome_name: str) -> tuple[int, int] | None:
    """An eight-way game score outcome as (winner, loser_points).

    `winner` is 1 or 2 -- the *player*, not the server, because that is how the
    book indexes this market ("П1:0", "40:П2"). Mapping to hold/break needs the
    serving side from the scoreboard, which this module deliberately does not
    guess at. Returns None for anything else.
    """
    name = (outcome_name or "").strip()
    m = _EXACT_HOME.match(name)
    if m:
        return 1, OUTCOME_POINTS[m.group(1)]
    m = _EXACT_AWAY.match(name)
    if m:
        return 2, OUTCOME_POINTS[m.group(1)]
    return None


def parse_server_score_outcome(outcome_name: str) -> tuple[bool, int | None] | None:
    """A five-way "server's score or break" outcome as (server_held, points).

    This market is indexed by the server, so it needs no scoreboard -- but it
    lumps every break into one outcome, so it carries strictly less information
    than the eight-way score. Returns (True, loser_points) for a hold,
    (False, None) for the break, None for anything else.
    """
    name = (outcome_name or "").strip()
    if name == BREAK_OUTCOME:
        return False, None
    m = _SERVER_SCORE.match(name)
    if m:
        return True, OUTCOME_POINTS[m.group(1)]
    return None
