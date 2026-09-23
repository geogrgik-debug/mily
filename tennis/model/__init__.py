"""Track B, step 3: turn a match into leak-safe per-game rows for the model."""
from tennis.model.game_rows import (
    GamePlay, GameRow, build_game_rows, game_winner,
)

__all__ = ["GamePlay", "GameRow", "build_game_rows", "game_winner"]
