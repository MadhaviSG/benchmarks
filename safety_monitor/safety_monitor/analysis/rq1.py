"""Pairing metrics: per-action critic scores vs trajectory-level ground truth."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from safety_monitor.analysis.stats import auroc, pearson, spearman
from safety_monitor.types import TrajectoryGroundTruth, TrajectoryLabels


@dataclass
class PairingMetrics:
    n: int
    pearson: float | None
    spearman: float | None
    auroc: float | None
    actor: str | None = None
    subset: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "pearson": self.pearson,
            "spearman": self.spearman,
            "auroc": self.auroc,
            "actor": self.actor,
            "subset": self.subset,
        }


def pairing_metrics(
    labelled: Sequence[TrajectoryLabels],
    truth: Sequence[TrajectoryGroundTruth],
    *,
    actor: str | None = None,
    subset: str | None = None,
) -> PairingMetrics:
    by_id = {t.instance_id: t for t in truth}
    scores: list[float] = []
    labels: list[int] = []
    for lab in labelled:
        gt = by_id.get(lab.instance_id)
        if gt is None or gt.rule_based not in (0, 1):
            continue
        n = lab.n_actions
        scores.append(lab.high_unsafe_count / n if n else 0.0)
        labels.append(int(gt.rule_based))
    if len(scores) < 2:
        return PairingMetrics(n=len(scores), pearson=None, spearman=None, auroc=None)
    return PairingMetrics(
        n=len(scores),
        pearson=_finite(pearson(scores, labels)),
        spearman=_finite(spearman(scores, labels)),
        auroc=_finite(auroc(scores, labels)),
        actor=actor,
        subset=subset,
    )


def _finite(value: float) -> float | None:
    if value != value:
        return None
    return float(value)
