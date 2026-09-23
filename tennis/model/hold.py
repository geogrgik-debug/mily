"""P(the server holds the next game), calibrated: the live state, a context residual, a beta map.

Track B, step 5 -- the audit's hybrid, version 1 (section 11.2):

    h      = MatchState.p_hold_next(server)                   the live state (tennis.state)
    logit q = logit(h) + c + beta . z                         z: tennis.features.context
    logit P = a ln q - b ln(1 - q) + d                        beta calibration (Kull et al., 2017)

The live state enters as an offset: its coefficient is fixed at one, and the
residual only adds to it. `c` and `beta` are a logistic regression with an L2
penalty (the intercept is not penalised) on the training years, one model for
Slams, tour and Challenger with the level among the features. The beta map is
fitted after, on later years the residual never saw; with a = b = 1, d = 0 it
is the identity. How the years are split and what the fit buys:
`tennis/eval/README.md`, "Calibration".

Live, `start_state` opens the match with the n0 the parameters were fitted
with, and `p_hold` prices the next game from the state and the scoreboard. The
parameters are a small JSON file, `hold_v1.json` next to this module, written
by `python -m tennis.eval calibrate`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np

from tennis.features.context import FEATURES, GameContext, vector
from tennis.state import MatchState

PARAMS_PATH = os.path.join(os.path.dirname(__file__), "hold_v1.json")
_EPS = 1e-12


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    return np.log(p) - np.log1p(-p)


def expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def fit_logit(X: np.ndarray, y: np.ndarray, offset: Optional[np.ndarray] = None,
              l2: float = 0.0, weight: Optional[np.ndarray] = None,
              iters: int = 100) -> np.ndarray:
    """Logistic regression by Newton's method: P(y) = expit(offset + X theta).

    Column 0 of `X` is taken as the intercept and is not penalised; the rest
    carry l2/2 |theta|^2. `weight` counts each row that many times (the
    bootstrap by match passes resampling counts through it).
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    off = np.zeros(len(y)) if offset is None else np.asarray(offset, dtype=float)
    w = np.ones(len(y)) if weight is None else np.asarray(weight, dtype=float)
    pen = np.full(X.shape[1], float(l2))
    pen[0] = 0.0
    theta = np.zeros(X.shape[1])
    for _ in range(iters):
        q = expit(off + X @ theta)
        grad = X.T @ (w * (q - y)) + pen * theta
        hess = (X * (w * q * (1 - q))[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        theta = theta - step
        if np.abs(step).max() < 1e-10:
            break
    return theta


def beta_fit(p: np.ndarray, y: np.ndarray, weight: Optional[np.ndarray] = None) -> Tuple[float, float, float]:
    """The beta calibration map (a, b, d): logit P = a ln p - b ln(1 - p) + d.

    Over the narrow range of hold forecasts ln p and ln(1 - p) move nearly
    together, so a and b are loosely pinned one by one while the map they make
    is not. If one comes out negative -- a map that would fall somewhere -- it
    is fixed at zero and the other two refitted, as Kull et al. do.
    """
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    X = np.column_stack([np.ones(len(p)), np.log(p), -np.log1p(-p)])
    d, a, b = fit_logit(X, y, weight=weight)
    if a < 0:
        (d, b), a = fit_logit(X[:, [0, 2]], y, weight=weight), 0.0
    elif b < 0:
        (d, a), b = fit_logit(X[:, [0, 1]], y, weight=weight), 0.0
    if a < 0 or b < 0 or a + b == 0:
        raise ValueError(f"beta calibration has no increasing map here (a={a:.3f}, b={b:.3f})")
    return float(a), float(b), float(d)


def beta_apply(p: np.ndarray, cal: Tuple[float, float, float]) -> np.ndarray:
    a, b, d = cal
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    return expit(a * np.log(p) - b * np.log1p(-p) + d)


@dataclass(frozen=True)
class HoldParams:
    """Everything `p_hold` needs, and where it came from."""

    features: Tuple[str, ...]
    intercept: float
    coef: Tuple[float, ...]
    calibration: Tuple[float, float, float]     # (a, b, d); (1, 1, 0) is the identity
    n0: Dict[str, float]                        # the live state's prior strength per level
    meta: Dict = field(default_factory=dict)    # years, row counts, the L2 chosen, the command

    def __post_init__(self) -> None:
        if tuple(self.features) != FEATURES:
            raise ValueError("these parameters were fitted on other features: "
                             f"{tuple(self.features)} vs {FEATURES}")
        if len(self.coef) != len(self.features):
            raise ValueError(f"{len(self.coef)} coefficients for {len(self.features)} features")

    def to_dict(self) -> dict:
        return {"features": list(self.features), "intercept": self.intercept,
                "coef": dict(zip(self.features, self.coef)),
                "calibration": dict(zip(("a", "b", "d"), self.calibration)),
                "n0": dict(self.n0), "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "HoldParams":
        feats = tuple(d["features"])
        return cls(features=feats, intercept=float(d["intercept"]),
                   coef=tuple(float(d["coef"][f]) for f in feats),
                   calibration=tuple(float(d["calibration"][k]) for k in ("a", "b", "d")),
                   n0={k: float(v) for k, v in d["n0"].items()}, meta=d.get("meta", {}))

    def save(self, path: str = PARAMS_PATH) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1)
            fh.write("\n")

    @classmethod
    def load(cls, path: str = PARAMS_PATH) -> "HoldParams":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def residual(h: np.ndarray, Z: np.ndarray, params: HoldParams) -> np.ndarray:
    """P(hold) with the context residual, before calibration."""
    return expit(logit(h) + params.intercept + np.asarray(Z, dtype=float) @ np.array(params.coef))


def predict(h: np.ndarray, Z: np.ndarray, params: HoldParams) -> np.ndarray:
    """The calibrated P(hold) for rows of live-state holds `h` and contexts `Z`."""
    return beta_apply(residual(h, Z, params), params.calibration)


def start_state(params: HoldParams, level: str, player_a: int, player_b: int,
                p_serve_a: float, p_serve_b: float) -> MatchState:
    """The live state before the first point, at the prior strength the
    parameters were fitted with -- another n0 would feed them another h."""
    return MatchState.start(player_a, player_b, p_serve_a, p_serve_b, n0=params.n0[level])


def p_hold(state: MatchState, server: int, context: GameContext, params: HoldParams) -> float:
    """P(`server` holds the next game, not started yet), calibrated.

    `state` holds every point played so far (`start_state`, then
    `after_point`); `context` is the scoreboard before this game.
    """
    h = np.array([state.p_hold_next(server)])
    return float(predict(h, vector(context)[None, :], params)[0])
