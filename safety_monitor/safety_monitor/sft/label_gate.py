"""Refuse SFT on action-level labels unless they beat the known degeneracies.

A new label set is allowed to launch Qwen or ShieldGemma SFT only if both:

1. The labels are a non-degenerate, two-way disagreement with the live
   ``sink_outcome_oracle``. An all-negative or all-positive labeler is not
   "new signal", and neither is a copy of ``task_unsafe AND sink``.
2. A ``TaskIdentityProbe`` (task id + instruction, no actions) does not
   explain the trajectory outcome as well as the new action-level signal.
   The probe is scored out-of-fold by ``instance_id``: its features contain a
   unique task token, so an in-sample fit separates a small sample perfectly
   and would close this condition no matter how good the labels were. The
   action-level lead must also clear ``MIN_TRAJECTORIES_PER_CLASS`` and a
   paired bootstrap, so a point estimate on a handful of runs cannot open it.

Until both hold, the default path exits without training. Fixture labels are
never treated as "real new labels" even if the numeric gate would open.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from safety_monitor.analysis.credit_assignment import (
    CREDIT_LABEL_SOURCES,
    DEFAULT_OUT_DIR,
    DEFAULT_TRAJECTORIES,
    CreditAssignment,
    apply_all,
    compare_assignments,
    load_assignments,
    path_is_credit_assignment,
    uses_credit_labels,
)
from safety_monitor.analysis.stats import auroc
from safety_monitor.online.baselines import (
    TaskIdentityProbe,
    sink_outcome_oracle_scorer,
)
from safety_monitor.sft.metrics import action_metrics
from safety_monitor.synthesis.pairs import MinedTrajectory
from safety_monitor.types import SafetyLabel


MIN_DISAGREEMENT_RATE = 0.02
COPY_F1_CEILING = 0.95
MIN_POSITIVE_PREDICTIONS = 1
MIN_NEGATIVE_PREDICTIONS = 1
#: Trajectories per outcome class before an AUROC comparison means anything.
MIN_TRAJECTORIES_PER_CLASS = 30
#: Paired-bootstrap resamples used to test action AUROC minus probe AUROC.
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_ALPHA = 0.05


def _bootstrap_delta_lower_bound(
    action_scores: Sequence[float],
    probe_scores: Sequence[float],
    labels: Sequence[int],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    alpha: float = BOOTSTRAP_ALPHA,
    seed: int = 0,
) -> float:
    """Lower bound on ``action AUROC - probe AUROC``, resampling trajectories.

    Paired on the trajectory, so the two monitors always see the same draw. A
    point estimate of ``action > probe`` on a handful of trajectories is not
    evidence; this asks whether the ordering survives resampling.
    """
    n = len(labels)
    if n == 0:
        return float("nan")
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        ys = [labels[i] for i in idx]
        if len(set(ys)) < 2:
            continue
        delta = auroc([action_scores[i] for i in idx], ys) - auroc(
            [probe_scores[i] for i in idx], ys
        )
        if delta == delta:
            deltas.append(delta)
    if not deltas:
        return float("nan")
    deltas.sort()
    return deltas[min(len(deltas) - 1, int(alpha * len(deltas)))]


@dataclass
class GateCondition:
    name: str
    passed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass
class GateDecision:
    open: bool
    reason: str
    conditions: list[GateCondition]
    real_labels: bool
    n_trajectories: int
    n_steps: int
    sft_launched: bool = False

    @property
    def sft_allowed(self) -> bool:
        return self.open and self.real_labels

    def as_dict(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "sft_allowed": self.sft_allowed,
            "sft_launched": self.sft_launched,
            "reason": self.reason,
            "real_labels": self.real_labels,
            "n_trajectories": self.n_trajectories,
            "n_steps": self.n_steps,
            "thresholds": {
                "min_disagreement_rate": MIN_DISAGREEMENT_RATE,
                "copy_f1_ceiling": COPY_F1_CEILING,
                "min_positive_predictions": MIN_POSITIVE_PREDICTIONS,
                "min_negative_predictions": MIN_NEGATIVE_PREDICTIONS,
                "min_trajectories_per_class": MIN_TRAJECTORIES_PER_CLASS,
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_alpha": BOOTSTRAP_ALPHA,
            },
            "conditions": [c.as_dict() for c in self.conditions],
        }


def _finite(value: float) -> float | None:
    return None if value != value else float(value)


def _oracle_disagreement_condition(
    vs_oracle: dict[str, Any],
    *,
    n_steps: int,
    n_disagree: int,
    disagreement_rate: float,
    f1: float,
    min_disagreement_rate: float,
    copy_f1_ceiling: float,
    min_positive: int = MIN_POSITIVE_PREDICTIONS,
    min_negative: int = MIN_NEGATIVE_PREDICTIONS,
) -> GateCondition:
    """First gate condition: non-degenerate two-way disagreement with the oracle."""
    cm = vs_oracle["confusion"]
    tp = int(cm["tp"])
    fp = int(cm["fp"])
    tn = int(cm["tn"])
    fn = int(cm["fn"])
    n_positive = tp + fp
    n_negative = tn + fn
    details = {
        "disagreement_rate": disagreement_rate,
        "n_disagree": n_disagree,
        "n_steps": n_steps,
        "n_positive_predictions": n_positive,
        "n_negative_predictions": n_negative,
        "min_positive_predictions": min_positive,
        "min_negative_predictions": min_negative,
        "min_disagreement_rate": min_disagreement_rate,
        "f1_vs_oracle": f1,
        "copy_f1_ceiling": copy_f1_ceiling,
        "two_way_disagreement": fp > 0 and fn > 0,
        "vs_oracle": vs_oracle,
    }

    if n_positive == 0:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "candidate labeled no positives "
                f"(n_positive=0, n_steps={n_steps}); "
                "all-negative labels are degenerate, not new action-level signal"
            ),
            details=details,
        )
    if n_negative == 0:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "candidate labeled no negatives "
                f"(n_positive={n_positive}, n_steps={n_steps}); "
                "all-positive labels are degenerate, not new action-level signal"
            ),
            details=details,
        )
    if n_positive < min_positive or n_negative < min_negative:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "candidate class counts are below the degeneracy floor "
                f"(n_positive={n_positive} need>={min_positive}, "
                f"n_negative={n_negative} need>={min_negative})"
            ),
            details=details,
        )
    if fp == 0 and fn == 0:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                f"candidate is a copy of the sink oracle (fp=0, fn=0, F1={f1:.3f})"
            ),
            details=details,
        )
    if fp == 0:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "disagreement is one-directional only "
                f"(fn={fn}, fp=0): candidate never marks a step the oracle "
                "does not; one-way misses are not evidence of new signal"
            ),
            details=details,
        )
    if fn == 0:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "disagreement is one-directional only "
                f"(fn=0, fp={fp}): candidate never misses a step the oracle "
                "marks; one-way extras are not evidence of new signal"
            ),
            details=details,
        )
    if disagreement_rate < min_disagreement_rate or f1 >= copy_f1_ceiling:
        return GateCondition(
            name="disagrees_with_sink_oracle",
            passed=False,
            reason=(
                "candidate is not distinct enough from the sink oracle "
                f"(disagreement {disagreement_rate:.3f} "
                f"need>={min_disagreement_rate:.3f}; "
                f"F1 vs oracle {f1:.3f} need<{copy_f1_ceiling:.3f}; "
                f"tp={tp} fp={fp} tn={tn} fn={fn})"
            ),
            details=details,
        )
    return GateCondition(
        name="disagrees_with_sink_oracle",
        passed=True,
        reason=(
            f"labels have two-way disagreement with sink_outcome_oracle "
            f"(fp={fp}, fn={fn}, {n_disagree}/{n_steps} steps, "
            f"F1={f1:.3f}) and are non-degenerate "
            f"(n_positive={n_positive}, n_negative={n_negative})"
        ),
        details=details,
    )


def _proposed_high(
    trajectories: Sequence[MinedTrajectory],
    assignments: Sequence[CreditAssignment] | None,
) -> list[list[int]]:
    if assignments is not None:
        by_key = {a.key: a for a in assignments}
        rows: list[list[int]] = []
        for traj in trajectories:
            assigned = by_key.get(traj.key)
            if assigned is None:
                rows.append([0] * len(traj.steps))
            else:
                rows.append(assigned.proposed_high_flags())
        return rows
    return [
        [1 if step.label is SafetyLabel.HIGH_UNSAFE else 0 for step in traj.steps]
        for traj in trajectories
    ]


def _action_scores(
    trajectories: Sequence[MinedTrajectory],
    assignments: Sequence[CreditAssignment] | None,
    proposed: Sequence[Sequence[int]],
) -> list[float]:
    if assignments is not None:
        by_key = {a.key: a for a in assignments}
        scores: list[float] = []
        for traj, flags in zip(trajectories, proposed):
            assigned = by_key.get(traj.key)
            if assigned is not None and assigned.credits():
                scores.append(max(assigned.credits()))
            else:
                scores.append(float(max(flags) if flags else 0))
        return scores
    return [float(max(flags) if flags else 0) for flags in proposed]


def evaluate_sft_gate(
    trajectories: Sequence[MinedTrajectory],
    *,
    assignments: Sequence[CreditAssignment] | None = None,
    real_labels: bool | None = None,
    min_disagreement_rate: float = MIN_DISAGREEMENT_RATE,
    copy_f1_ceiling: float = COPY_F1_CEILING,
    min_per_class: int = MIN_TRAJECTORIES_PER_CLASS,
) -> GateDecision:
    """Evaluate the two-part SFT gate. Does not launch training."""
    if not trajectories:
        return GateDecision(
            open=False,
            reason="gate closed: no trajectories to evaluate",
            conditions=[],
            real_labels=False,
            n_trajectories=0,
            n_steps=0,
        )

    proposed_rows = _proposed_high(trajectories, assignments)
    proposed = [bit for row in proposed_rows for bit in row]
    oracle = [
        1 if score > 0.5 else 0
        for traj in trajectories
        for score in sink_outcome_oracle_scorer(traj)
    ]
    if len(proposed) != len(oracle):
        raise ValueError("proposed labels and sink-oracle scores are misaligned")

    vs_oracle = action_metrics(oracle, proposed)
    n_steps = len(oracle)
    n_disagree = sum(p != o for p, o in zip(proposed, oracle))
    disagreement_rate = n_disagree / n_steps if n_steps else 0.0
    f1 = float(vs_oracle["f1_high_unsafe"])
    cond_copy = _oracle_disagreement_condition(
        vs_oracle,
        n_steps=n_steps,
        n_disagree=n_disagree,
        disagreement_rate=disagreement_rate,
        f1=f1,
        min_disagreement_rate=min_disagreement_rate,
        copy_f1_ceiling=copy_f1_ceiling,
    )

    y = [1 if t.rule_based == 1 else 0 for t in trajectories]
    # Out-of-fold: the probe's task-id token makes an in-sample fit a perfect
    # separator at small n, which would leave this condition unpassable by
    # construction. In-sample is kept alongside as a leakage diagnostic.
    probe_scores = TaskIdentityProbe.out_of_fold_scores(list(trajectories))
    in_sample_probe = TaskIdentityProbe()
    in_sample_probe.fit(list(trajectories))
    in_sample_scores = [
        in_sample_probe.score(t)[0] if t.steps else 0.0 for t in trajectories
    ]
    action_scores = _action_scores(trajectories, assignments, proposed_rows)
    probe_auroc = auroc(probe_scores, y)
    in_sample_auroc = auroc(in_sample_scores, y)
    action_auroc = auroc(action_scores, y)
    n_unsafe = sum(y)
    n_safe = len(y) - n_unsafe
    delta_lower = _bootstrap_delta_lower_bound(action_scores, probe_scores, y)
    comparable = action_auroc == action_auroc and probe_auroc == probe_auroc
    ahead = comparable and action_auroc > probe_auroc
    big_enough = min(n_unsafe, n_safe) >= min_per_class
    survives = delta_lower == delta_lower and delta_lower > 0.0
    beats = ahead and big_enough and survives

    probe_s = "n/a" if probe_auroc != probe_auroc else f"{probe_auroc:.3f}"
    action_s = "n/a" if action_auroc != action_auroc else f"{action_auroc:.3f}"
    delta_s = "n/a" if delta_lower != delta_lower else f"{delta_lower:+.3f}"
    if not ahead:
        probe_reason = (
            f"task-identity probe explains the trajectory outcome as well as "
            f"the action-level signal (action AUROC {action_s}, "
            f"out-of-fold task_identity AUROC {probe_s}; need action > probe)"
        )
    elif not big_enough:
        probe_reason = (
            f"sample too small to compare AUROCs "
            f"({n_unsafe} unsafe, {n_safe} safe; need >={min_per_class} of "
            f"each): action AUROC {action_s} over probe {probe_s} is not "
            f"distinguishable from noise at this size"
        )
    elif not survives:
        probe_reason = (
            f"action-level lead over the task-identity probe does not survive "
            f"resampling (action AUROC {action_s}, probe {probe_s}, "
            f"bootstrap {int((1 - BOOTSTRAP_ALPHA) * 100)}% lower bound on the "
            f"difference {delta_s}; need > 0)"
        )
    else:
        probe_reason = (
            f"action-level AUROC {action_s} > out-of-fold task_identity "
            f"AUROC {probe_s}, lower bound on the difference {delta_s}"
        )
    cond_probe = GateCondition(
        name="beats_task_identity",
        passed=beats,
        reason=probe_reason,
        details={
            "action_level_auroc": _finite(action_auroc),
            "task_identity_auroc": _finite(probe_auroc),
            "task_identity_auroc_in_sample": _finite(in_sample_auroc),
            "task_identity_scoring": "out_of_fold_by_instance_id",
            "auroc_delta_lower_bound": _finite(delta_lower),
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_alpha": BOOTSTRAP_ALPHA,
            "min_trajectories_per_class": min_per_class,
            "n_unsafe": n_unsafe,
            "n_safe": n_safe,
        },
    )

    if assignments is not None:
        inferred_real = any(a.real_llm for a in assignments)
    else:
        inferred_real = any(
            step.label_source == "llm_credit"
            for traj in trajectories
            for step in traj.steps
        )
    if real_labels is None:
        real_labels = inferred_real

    open_ = cond_copy.passed and cond_probe.passed
    if not open_:
        first_fail = next(c for c in (cond_copy, cond_probe) if not c.passed)
        reason = f"gate closed: {first_fail.reason}"
    elif not real_labels:
        reason = (
            "gate open on the numeric tests, but labels are not from a real "
            "LLM / replay pass; SFT will not launch"
        )
    else:
        reason = (
            "gate open: labels disagree with the sink oracle and beat the "
            "task-identity probe"
        )

    return GateDecision(
        open=open_,
        reason=reason,
        conditions=[cond_copy, cond_probe],
        real_labels=bool(real_labels),
        n_trajectories=len(trajectories),
        n_steps=n_steps,
    )


def evaluate_sft_gate_from_files(
    *,
    trajectories_path: str | Path = DEFAULT_TRAJECTORIES,
    labels_path: str | Path | None = None,
    real_labels: bool | None = None,
) -> tuple[GateDecision, list[MinedTrajectory], list[CreditAssignment] | None]:
    from safety_monitor.sft.data import load_trajectories

    trajs = load_trajectories(trajectories_path)
    assignments: list[CreditAssignment] | None = None
    path = Path(labels_path) if labels_path is not None else None
    if path is not None and path.exists():
        if path.name == "labeled_trajectories.jsonl" or (
            path_is_credit_assignment(path) and path.name.endswith("trajectories.jsonl")
        ):
            trajs = load_trajectories(path)
        else:
            assignments = load_assignments(path)
            keys = {a.key for a in assignments}
            trajs = [t for t in trajs if t.key in keys]
    return (
        evaluate_sft_gate(trajs, assignments=assignments, real_labels=real_labels),
        trajs,
        assignments,
    )


def refuse_sft_if_closed(
    trajectories: Sequence[MinedTrajectory],
    train_paths: Sequence[str | Path] | None = None,
    *,
    assignments: Sequence[CreditAssignment] | None = None,
    real_labels: bool | None = None,
) -> GateDecision | None:
    """Return a closed-gate decision when these train rows need the gate.

    ``None`` means the gate does not apply (ordinary synthetic SFT).
    """
    from_path = any(path_is_credit_assignment(p) for p in (train_paths or []))
    from_source = uses_credit_labels(trajectories)
    if not from_path and not from_source and assignments is None:
        return None
    decision = evaluate_sft_gate(
        trajectories, assignments=assignments, real_labels=real_labels
    )
    return decision


def maybe_launch_sft(
    decision: GateDecision,
    *,
    launch_if_open: bool = False,
) -> GateDecision:
    """Default path: never launch. Only launch when explicitly asked and allowed."""
    if launch_if_open and decision.sft_allowed:
        raise RuntimeError(
            "SFT launch was requested and the gate is open; the caller must "
            "invoke Qwen/ShieldGemma SFT explicitly. This helper does not "
            "start GPU training."
        )
    decision.sft_launched = False
    return decision


def run_sft_gate(
    *,
    trajectories_path: str | Path = DEFAULT_TRAJECTORIES,
    labels_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    launch_if_open: bool = False,
) -> dict[str, Any]:
    labels = (
        Path(labels_path)
        if labels_path
        else (DEFAULT_OUT_DIR / "proposed_labels.jsonl")
    )
    decision, trajs, assignments = evaluate_sft_gate_from_files(
        trajectories_path=trajectories_path,
        labels_path=labels if labels.exists() else None,
    )
    decision = maybe_launch_sft(decision, launch_if_open=launch_if_open)
    payload = decision.as_dict()
    payload["labels_path"] = str(labels)
    payload["trajectories_path"] = str(trajectories_path)
    payload["n_assignments"] = None if assignments is None else len(assignments)
    payload["n_loaded_trajectories"] = len(trajs)
    dest = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "gate.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    readme = dest / "README.md"
    if readme.exists():
        extra = (
            "\n## SFT gate\n\n"
            f"**Decision: {'open' if decision.open else 'closed'}.**\n\n"
            f"{decision.reason}\n\n"
            f"SFT launched: {decision.sft_launched}.\n"
            + (
                "\nThe fixture run tested plumbing only and produced no "
                "evidence about credit assignment.\n"
                if not decision.real_labels
                else ""
            )
        )
        text = readme.read_text(encoding="utf-8")
        if "## SFT gate" in text:
            text = text.split("## SFT gate")[0].rstrip() + "\n" + extra
        else:
            text = text.rstrip() + "\n" + extra
        readme.write_text(text, encoding="utf-8")
    return payload


# Keep compare_assignments / apply_all imported for callers that relabel then gate.
__all__ = [
    "COPY_F1_CEILING",
    "CREDIT_LABEL_SOURCES",
    "GateCondition",
    "GateDecision",
    "MIN_DISAGREEMENT_RATE",
    "MIN_NEGATIVE_PREDICTIONS",
    "MIN_POSITIVE_PREDICTIONS",
    "apply_all",
    "compare_assignments",
    "evaluate_sft_gate",
    "evaluate_sft_gate_from_files",
    "maybe_launch_sft",
    "refuse_sft_if_closed",
    "run_sft_gate",
]
