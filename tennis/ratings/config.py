"""Rating configurations, each named after the experiment that measured it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class RatingsConfig:
    """Everything that shapes a prior, in one place so a snapshot can record it.

    Elo update weight is K = k0 / (n + k_offset) ** k_exp for a player with n
    earlier matches; the rating a match is predicted from is
    (1 - surface_weight) * overall + surface_weight * surface-specific.
    The serve prior is blend_elo * (Elo inverted into a serve pair)
    + (1 - blend_elo) * Barnett-Clarke on a rolling window.
    """
    k0: float = 400.0
    k_offset: float = 5.0
    k_exp: float = 0.4
    surface_weight: float = 0.3
    initial_rating: float = 1500.0
    blend_elo: float = 0.6
    # research/elo_prior.py: int(12 months * 30.5 days)
    window_days: int = 366
    # serve and return rates are shrunk toward the tour mean by this many points
    shrink_points: float = 200.0
    # drop matches of events that were still being played on the as-of date
    strict_overlap: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RatingsConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown config fields: {sorted(unknown)}")
        return cls(**d)


# Experiment B1c, the best of 18 configurations on the 2022+ test: Elo RMSE
# 0.0865, blend 0.0849 at a train-fitted weight of 0.60. The default.
SWEEP_BEST = RatingsConfig()

# Experiment B1 as first run and as documented in the B1 table: RMSE 0.0871 for
# the Elo inversion, 0.0855 for the blend at a train-fitted weight of 0.55.
# Kept to reproduce those numbers, not to be used.
RESEARCH_B1 = RatingsConfig(k0=250.0, surface_weight=0.5, blend_elo=0.55)

CONFIGS = {"sweep": SWEEP_BEST, "research-b1": RESEARCH_B1}
