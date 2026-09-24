"""Compare two independent credit-assignment annotators action by action.

The question this answers is narrow. If two frontier models, given the same
trajectories and the same rubric, disagree about which actions caused the
grader outcome, then neither label set is a training signal no matter how it
scores against the sink oracle. Agreement is a precondition, not evidence of
correctness: two models can agree and still both be wrong.

Consensus is deliberately strict. ``consensus_high`` requires both annotators
to mark an action, so a disagreement never silently becomes a positive label.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from safety_monitor.analysis.credit_assignment import (
    CreditAssignment,
    ProposedAction,
    load_assignments,
)
from safety_monitor.analysis.stats import spearman


@dataclass
class AgreementReport:
    annotator_a: str
    annotator_b: str
    n_trajectories: int
    n_steps: int
    contributed: dict[str, Any]
    proposed_high: dict[str, Any]
    rank: dict[str, Any]
    disagreements: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "annotator_a": self.annotator_a,
            "annotator_b": self.annotator_b,
            "n_trajectories": self.n_trajectories,
            "n_steps": self.n_steps,
            "contributed": self.contributed,
            "proposed_high": self.proposed_high,
            "rank": self.rank,
            "n_disagreements": len(self.disagreements),
            "disagreements": self.disagreements,
        }


def cohen_kappa(a: Sequence[int], b: Sequence[int]) -> float:
    """Chance-corrected agreement on a binary flag.

    Raw agreement is misleading here: both annotators mark few actions, so a
    pair that never agrees on a positive can still post 0.9 raw agreement.
    """
    if len(a) != len(b) or not a:
        return float("nan")
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    expected = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if expected >= 1.0:
        return float("nan")
    return (observed - expected) / (1 - expected)


def _binary_stats(a: Sequence[int], b: Sequence[int]) -> dict[str, Any]:
    n = len(a)
    both = sum(1 for x, y in zip(a, b) if x == 1 and y == 1)
    only_a = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    only_b = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    neither = n - both - only_a - only_b
    union = both + only_a + only_b
    return {
        "n": n,
        "raw_agreement": (both + neither) / n if n else float("nan"),
        "cohen_kappa": cohen_kappa(a, b),
        "jaccard_on_positives": both / union if union else float("nan"),
        "both": both,
        "only_a": only_a,
        "only_b": only_b,
        "neither": neither,
        "n_positive_a": sum(a),
        "n_positive_b": sum(b),
    }


def _aligned(
    a: CreditAssignment, b: CreditAssignment
) -> list[tuple[ProposedAction, ProposedAction]]:
    lookup = {p.action_id: p for p in b.proposed}
    pairs: list[tuple[ProposedAction, ProposedAction]] = []
    for step in a.proposed:
        other = lookup.get(step.action_id)
        if other is not None:
            pairs.append((step, other))
    return pairs


def compare_annotators(
    a_assignments: Sequence[CreditAssignment],
    b_assignments: Sequence[CreditAssignment],
    *,
    annotator_a: str = "a",
    annotator_b: str = "b",
    max_disagreements: int = 40,
) -> AgreementReport:
    by_key_b = {x.key: x for x in b_assignments}
    shared = [x for x in a_assignments if x.key in by_key_b]

    contributed_a: list[int] = []
    contributed_b: list[int] = []
    high_a: list[int] = []
    high_b: list[int] = []
    rank_rhos: list[float] = []
    disagreements: list[dict[str, Any]] = []

    for assigned in shared:
        other = by_key_b[assigned.key]
        pairs = _aligned(assigned, other)
        if not pairs:
            continue
        for step_a, step_b in pairs:
            contributed_a.append(1 if step_a.contributed else 0)
            contributed_b.append(1 if step_b.contributed else 0)
            high_a.append(1 if step_a.proposed_high else 0)
            high_b.append(1 if step_b.proposed_high else 0)
            if step_a.contributed != step_b.contributed:
                if len(disagreements) < max_disagreements:
                    disagreements.append(
                        {
                            "key": assigned.key,
                            "action_id": step_a.action_id,
                            f"{annotator_a}_credit": step_a.credit,
                            f"{annotator_b}_credit": step_b.credit,
                            f"{annotator_a}_contributed": step_a.contributed,
                            f"{annotator_b}_contributed": step_b.contributed,
                        }
                    )
        # Rank agreement is per trajectory: credit is only meaningful
        # relative to the other actions in the same run.
        if len(pairs) >= 2:
            rho = spearman([p.credit for p, _ in pairs], [q.credit for _, q in pairs])
            if rho == rho:
                rank_rhos.append(rho)

    mean_rho = sum(rank_rhos) / len(rank_rhos) if rank_rhos else float("nan")
    return AgreementReport(
        annotator_a=annotator_a,
        annotator_b=annotator_b,
        n_trajectories=len(shared),
        n_steps=len(contributed_a),
        contributed=_binary_stats(contributed_a, contributed_b),
        proposed_high=_binary_stats(high_a, high_b),
        rank={
            "mean_spearman_within_trajectory": mean_rho,
            "n_trajectories_scored": len(rank_rhos),
            "n_trajectories_unscorable": len(shared) - len(rank_rhos),
        },
        disagreements=disagreements,
    )


def consensus_assignments(
    a_assignments: Sequence[CreditAssignment],
    b_assignments: Sequence[CreditAssignment],
    *,
    completer_id: str = "consensus",
) -> list[CreditAssignment]:
    """Actions both annotators marked. Credit is the mean of the two.

    Strict AND on ``contributed``: an action only one model flagged is not
    consensus evidence, and letting it through would make the consensus set
    the union of two noisy labelers.
    """
    by_key_b = {x.key: x for x in b_assignments}
    out: list[CreditAssignment] = []
    for assigned in a_assignments:
        other = by_key_b.get(assigned.key)
        if other is None:
            continue
        lookup = {p.action_id: p for p in other.proposed}
        proposed: list[ProposedAction] = []
        for step in assigned.proposed:
            match = lookup.get(step.action_id)
            if match is None:
                proposed.append(
                    ProposedAction(
                        action_id=step.action_id,
                        credit=0.0,
                        contributed=False,
                        proposed_high=False,
                        rationale="no matching action from the second annotator",
                    )
                )
                continue
            agreed = step.contributed and match.contributed
            proposed.append(
                ProposedAction(
                    action_id=step.action_id,
                    credit=(step.credit + match.credit) / 2.0,
                    contributed=agreed,
                    proposed_high=bool(assigned.rule_based == 1 and agreed),
                    rationale=(
                        "both annotators marked this action"
                        if agreed
                        else "not marked by both annotators"
                    ),
                )
            )
        out.append(
            CreditAssignment(
                key=assigned.key,
                instance_id=assigned.instance_id,
                rule_based=assigned.rule_based,
                proposed=proposed,
                raw_response="",
                completer_id=completer_id,
                real_llm=assigned.real_llm and other.real_llm,
            )
        )
    return out


def format_agreement(report: AgreementReport) -> str:
    lines = [
        f"annotator_a: {report.annotator_a}",
        f"annotator_b: {report.annotator_b}",
        f"n_trajectories: {report.n_trajectories}",
        f"n_steps: {report.n_steps}",
        "",
        "contributed flag",
    ]
    for label, stats in (
        ("contributed", report.contributed),
        ("proposed_high", report.proposed_high),
    ):
        lines.append(
            f"  {label}: raw={stats['raw_agreement']:.3f} "
            f"kappa={stats['cohen_kappa']:.3f} "
            f"jaccard={stats['jaccard_on_positives']:.3f} "
            f"both={stats['both']} only_a={stats['only_a']} "
            f"only_b={stats['only_b']} neither={stats['neither']}"
        )
    rho = report.rank["mean_spearman_within_trajectory"]
    rho_s = "n/a" if rho != rho else f"{rho:.3f}"
    lines.append("")
    lines.append(f"within-trajectory credit rank: mean spearman {rho_s}")
    lines.append(
        f"  scored on {report.rank['n_trajectories_scored']} trajectories, "
        f"{report.rank['n_trajectories_unscorable']} too short or constant"
    )
    lines.append("")
    lines.append(f"disagreeing actions shown: {len(report.disagreements)}")
    return "\n".join(lines)


def run_agreement(
    *,
    a_path: str | Path,
    b_path: str | Path,
    out_dir: str | Path,
    annotator_a: str = "a",
    annotator_b: str = "b",
) -> dict[str, Any]:
    a_assignments = load_assignments(a_path)
    b_assignments = load_assignments(b_path)
    report = compare_annotators(
        a_assignments,
        b_assignments,
        annotator_a=annotator_a,
        annotator_b=annotator_b,
    )
    consensus = consensus_assignments(a_assignments, b_assignments)

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "agreement.json").write_text(
        json.dumps(report.as_dict(), indent=2), encoding="utf-8"
    )
    (dest / "agreement.txt").write_text(
        format_agreement(report) + "\n", encoding="utf-8"
    )
    consensus_path = dest / "consensus_labels.jsonl"
    with consensus_path.open("w", encoding="utf-8") as handle:
        for assigned in consensus:
            handle.write(
                json.dumps(
                    {
                        "key": assigned.key,
                        "instance_id": assigned.instance_id,
                        "rule_based": assigned.rule_based,
                        "completer_id": assigned.completer_id,
                        "real_llm": assigned.real_llm,
                        "steps": [
                            {
                                "action_id": p.action_id,
                                "credit": p.credit,
                                "contributed": p.contributed,
                                "proposed_high": p.proposed_high,
                                "rationale": p.rationale,
                            }
                            for p in assigned.proposed
                        ],
                    }
                )
                + "\n"
            )
    return {
        "report": report.as_dict(),
        "consensus_labels": str(consensus_path),
        "agreement_json": str(dest / "agreement.json"),
        "agreement_txt": str(dest / "agreement.txt"),
    }


__all__ = [
    "AgreementReport",
    "cohen_kappa",
    "compare_annotators",
    "consensus_assignments",
    "format_agreement",
    "run_agreement",
]
