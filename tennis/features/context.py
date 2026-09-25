"""A service game's context: what the scoreboard says before its first point.

Track B, step 5. The audit's hybrid (section 11.2) adds a linear term in the
context to the live state's logit: logit P(hold) = logit(h_struct) + c + beta.z.
This module is z. It holds only what is known before the game's first point --
the level and surface of the event, the set and game score, and how the last
two games went -- so the context of a game cannot carry its own outcome.

One object serves both worlds. Offline it is read off a `tennis.model.GameRow`
(`GameContext.from_row`), live the feed's score fills it directly, and
`vector` turns either into the same numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

LEVELS = ("slam", "tour", "chall")

# Tour and hard court are the base: their effects sit in the intercept. Carpet
# counts as hard -- an indoor fast court, and all but gone from the tour.
FEATURES = (
    "slam", "chall", "clay", "grass",
    "first_service_game",    # the server has not served yet: the live state is still the prior
    "service_games_so_far",  # his service games before this one, / 10
    "later_set",             # set number minus one
    "games_in_set",          # games played in this set, / 10
    "serving_for_set", "serving_to_stay",
    "sets_ahead", "sets_behind", "deciding_set",
    "just_broke",            # he broke in the game just before
    "was_broken",            # his own last service game was lost
)


@dataclass(frozen=True)
class GameContext:
    """The scoreboard before a service game, from the server's side."""

    level: str               # 'slam', 'tour' or 'chall'
    surface: str             # Sackmann's: 'Hard', 'Clay', 'Grass', 'Carpet'
    best_of: int
    set_no: int              # 1-based
    server_games: int        # games in this set so far
    returner_games: int
    server_sets: int         # sets won before this one
    returner_sets: int
    served_before: int       # the server's service games so far in the match
    just_broke: bool
    was_broken: bool

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"level is one of {LEVELS}, not {self.level!r}")
        if self.best_of not in (3, 5):
            raise ValueError(f"best_of is 3 or 5, not {self.best_of!r}")
        counts = (self.server_games, self.returner_games, self.server_sets,
                  self.returner_sets, self.served_before)
        if self.set_no < 1 or min(counts) < 0:
            raise ValueError(f"a score cannot be negative: {self}")

    @classmethod
    def from_row(cls, row, level: str, surface: str, best_of: int) -> "GameContext":
        """The context of a `tennis.model.GameRow`; the match's level, surface
        and format come from outside, as the row is about one game."""
        return cls(level=level, surface=surface, best_of=best_of, set_no=row.set_no,
                   server_games=(row.games_in_set + row.game_diff) // 2,
                   returner_games=(row.games_in_set - row.game_diff) // 2,
                   server_sets=row.server_sets, returner_sets=row.returner_sets,
                   served_before=row.cum_games, just_broke=bool(row.server_just_broke),
                   was_broken=bool(row.prev_broken))


def vector(c: GameContext) -> np.ndarray:
    """The context as numbers, in the order of `FEATURES`."""
    g, r = c.server_games, c.returner_games
    return np.array([
        c.level == "slam", c.level == "chall",
        c.surface == "Clay", c.surface == "Grass",
        c.served_before == 0, c.served_before / 10.0,
        c.set_no - 1, (g + r) / 10.0,
        g >= 5 and g - r >= 1, r >= 5 and r - g >= 1,
        c.server_sets > c.returner_sets, c.server_sets < c.returner_sets,
        c.set_no == c.best_of,
        c.just_broke, c.was_broken,
    ], dtype=float)


def matrix(contexts: Iterable[GameContext]) -> np.ndarray:
    """One row of `vector` per context."""
    rows = [vector(c) for c in contexts]
    return np.array(rows, dtype=float).reshape(len(rows), len(FEATURES))
