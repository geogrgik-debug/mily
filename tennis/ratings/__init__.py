"""Elo and as-of serve priors, with state that survives a process.

Lifted from `research/elo_prior.py` (build_elo, rolling_priors and the prior
arithmetic of evaluate), configured from the B1c sweep, and made loadable: a
`RatingsSnapshot` is built once from the match files and read back by the live
process instead of replaying 270 thousand matches. Nothing here imports
`research/`.
"""
from tennis.ratings.config import CONFIGS, RESEARCH_B1, SWEEP_BEST, RatingsConfig
from tennis.ratings.elo import EloState, build_elo
from tennis.ratings.prior import BaselineTable, Prior, barnett_clarke, historical_priors
from tennis.ratings.sackmann import Matches, from_records, load_matches
from tennis.ratings.serve import ServeHistory, rolling_windows
from tennis.ratings.snapshot import RatingsSnapshot

__all__ = [
    "CONFIGS", "RESEARCH_B1", "SWEEP_BEST", "RatingsConfig",
    "EloState", "build_elo",
    "BaselineTable", "Prior", "barnett_clarke", "historical_priors",
    "Matches", "from_records", "load_matches",
    "ServeHistory", "rolling_windows",
    "RatingsSnapshot",
]
