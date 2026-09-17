"""Prefix-causal aggregators turning per-action scores into running credence.

Every aggregator maps a sequence of per-action scores in ``[0, 1]`` to a
sequence of the same length, where element ``k`` may only depend on scores
``0..k``. That constraint is what makes the output replayable as an online
monitor: ``credence[k]`` is what the monitor would have believed after seeing
action ``k`` and nothing more.

``assert_prefix_causal`` is exported so tests (and new aggregators) can check
the property rather than trusting it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence


Aggregator = Callable[[Sequence[float]], list[float]]

EPS = 1e-6


def _clip(score: float) -> float:
    if score != score:  # NaN -> treat as no evidence, but stay in the open interval
        return EPS
    return min(1.0 - EPS, max(EPS, float(score)))


def cummax(scores: Sequence[float]) -> list[float]:
    """Running maximum: credence never falls once a bad action is seen.

    The natural baseline for a monitor that escalates on its single most
    suspicious observation and never forgives.
    """
    out: list[float] = []
    best = 0.0
    for score in scores:
        best = max(best, _clip(score))
        out.append(best)
    return out


def noisy_or(scores: Sequence[float]) -> list[float]:
    """``1 - prod(1 - s)``: independent-evidence accumulation.

    Many mildly suspicious actions add up, unlike :func:`cummax`. Saturates
    quickly on long trajectories, which is exactly the failure mode the
    length-normalised aggregators below avoid.
    """
    out: list[float] = []
    surviving = 1.0
    for score in scores:
        surviving *= 1.0 - _clip(score)
        out.append(1.0 - surviving)
    return out


def ew_decay(scores: Sequence[float], *, half_life: float = 3.0) -> list[float]:
    """Exponentially-weighted mean with recent actions weighted highest.

    ``half_life`` is in actions: evidence from ``half_life`` steps ago counts
    half as much as the current action. Unlike :func:`noisy_or` this can fall,
    so a monitor can be talked back down by a run of benign actions.
    """
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    decay = 0.5 ** (1.0 / half_life)
    out: list[float] = []
    weighted = 0.0
    total = 0.0
    for score in scores:
        weighted = weighted * decay + _clip(score)
        total = total * decay + 1.0
        out.append(weighted / total if total else 0.0)
    return out


def logodds_sum(scores: Sequence[float], *, prior: float = 0.5) -> list[float]:
    """Sum of per-action log-odds, squashed back through a sigmoid.

    The calibrated-Bayes version of :func:`noisy_or`: each action contributes
    its log-likelihood-ratio against ``prior``, so scores below the prior push
    credence *down*. Requires the underlying scores to be calibrated to be
    meaningful; on uncalibrated scores it mostly reproduces ``noisy_or``.
    """
    prior = _clip(prior)
    prior_logit = math.log(prior / (1.0 - prior))
    out: list[float] = []
    logit = prior_logit
    for score in scores:
        s = _clip(score)
        logit += math.log(s / (1.0 - s)) - prior_logit
        out.append(1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit)))))
    return out


AGGREGATORS: dict[str, Aggregator] = {
    "cummax": cummax,
    "noisy_or": noisy_or,
    "ew_decay": ew_decay,
    "logodds_sum": logodds_sum,
}


def get_aggregator(name: str) -> Aggregator:
    try:
        return AGGREGATORS[name]
    except KeyError:
        raise ValueError(
            f"unknown aggregator {name!r}; choose from {sorted(AGGREGATORS)}"
        ) from None


def assert_prefix_causal(aggregator: Aggregator, scores: Sequence[float]) -> None:
    """Raise if ``aggregator`` lets later scores change an earlier credence.

    An aggregator that peeks ahead would silently inflate every lead-time
    number in :mod:`safety_monitor.online.metrics`, so this is checked rather
    than assumed.
    """
    full = aggregator(scores)
    if len(full) != len(scores):
        raise AssertionError(
            f"aggregator returned {len(full)} credences for {len(scores)} scores"
        )
    for k in range(1, len(scores) + 1):
        prefix = aggregator(scores[:k])
        if len(prefix) != k:
            raise AssertionError(f"prefix length {len(prefix)} != {k}")
        if abs(prefix[k - 1] - full[k - 1]) > 1e-9:
            raise AssertionError(
                f"credence at step {k - 1} changed from {prefix[k - 1]!r} to "
                f"{full[k - 1]!r} once later actions were revealed: "
                "aggregator is not prefix-causal"
            )
