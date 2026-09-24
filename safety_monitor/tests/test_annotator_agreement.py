"""Two-annotator agreement and strict consensus."""

from __future__ import annotations

import math

import pytest
from safety_monitor.analysis.annotator_agreement import (
    cohen_kappa,
    compare_annotators,
    consensus_assignments,
    run_agreement,
)
from safety_monitor.analysis.credit_assignment import (
    CreditAssignment,
    ProposedAction,
)


def _assignment(
    key: str,
    marks: list[tuple[str, bool, float]],
    *,
    rule_based: int = 1,
    completer_id: str = "test",
) -> CreditAssignment:
    return CreditAssignment(
        key=key,
        instance_id=key.split("::")[-1],
        rule_based=rule_based,
        proposed=[
            ProposedAction(
                action_id=action_id,
                credit=credit,
                contributed=contributed,
                proposed_high=bool(rule_based == 1 and contributed),
            )
            for action_id, contributed, credit in marks
        ],
        completer_id=completer_id,
        real_llm=True,
    )


def test_kappa_punishes_agreement_that_is_only_shared_silence() -> None:
    """Raw agreement is high when both annotators mark almost nothing."""
    a = [0] * 98 + [1, 0]
    b = [0] * 98 + [0, 1]
    raw = sum(1 for x, y in zip(a, b) if x == y) / len(a)
    assert raw > 0.95
    assert cohen_kappa(a, b) < 0.0


def test_identical_annotators_have_kappa_one() -> None:
    a = [1, 0, 0, 1, 0]
    assert cohen_kappa(a, list(a)) == 1.0


def test_agreement_counts_both_directions_of_disagreement() -> None:
    a = [_assignment("r::t1", [("s0", True, 0.9), ("s1", False, 0.0)])]
    b = [_assignment("r::t1", [("s0", False, 0.1), ("s1", True, 0.8)])]

    report = compare_annotators(a, b, annotator_a="sonnet", annotator_b="gemini")

    assert report.n_trajectories == 1
    assert report.n_steps == 2
    assert report.contributed["only_a"] == 1
    assert report.contributed["only_b"] == 1
    assert report.contributed["both"] == 0
    assert report.contributed["jaccard_on_positives"] == 0.0
    assert len(report.disagreements) == 2


def test_consensus_requires_both_annotators() -> None:
    a = [
        _assignment(
            "r::t1",
            [("s0", True, 0.9), ("s1", True, 0.6), ("s2", False, 0.0)],
        )
    ]
    b = [
        _assignment(
            "r::t1",
            [("s0", True, 0.7), ("s1", False, 0.2), ("s2", False, 0.0)],
        )
    ]

    consensus = consensus_assignments(a, b)

    flags = consensus[0].proposed_high_flags()
    assert flags == [1, 0, 0], "only the action both marked survives"
    assert consensus[0].proposed[0].credit == 0.8, "credit is the mean"


def test_consensus_stays_negative_on_a_safe_trajectory() -> None:
    """proposed_high is outcome-gated, so a safe run cannot yield positives."""
    a = [_assignment("r::t2", [("s0", True, 0.9)], rule_based=0)]
    b = [_assignment("r::t2", [("s0", True, 0.9)], rule_based=0)]

    consensus = consensus_assignments(a, b)

    assert consensus[0].proposed[0].contributed is True
    assert consensus[0].proposed_high_flags() == [0]


def test_rank_agreement_is_computed_within_each_trajectory() -> None:
    a = [_assignment("r::t1", [("s0", True, 0.9), ("s1", False, 0.1)])]
    b = [_assignment("r::t1", [("s0", True, 0.8), ("s1", False, 0.2)])]

    report = compare_annotators(a, b)

    assert report.rank["n_trajectories_scored"] == 1
    assert report.rank["mean_spearman_within_trajectory"] == pytest.approx(1.0)


def test_unmatched_keys_are_skipped_not_guessed() -> None:
    a = [
        _assignment("r::t1", [("s0", True, 0.9)]),
        _assignment("r::only_in_a", [("s0", True, 0.9)]),
    ]
    b = [_assignment("r::t1", [("s0", True, 0.9)])]

    report = compare_annotators(a, b)

    assert report.n_trajectories == 1
    assert len(consensus_assignments(a, b)) == 1


def test_run_agreement_writes_consensus_and_report(tmp_path) -> None:
    import json

    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    for path, contributed in ((a_path, True), (b_path, False)):
        row = {
            "key": "r::t1",
            "instance_id": "t1",
            "rule_based": 1,
            "completer_id": "test",
            "real_llm": True,
            "steps": [
                {
                    "action_id": "s0",
                    "credit": 0.9 if contributed else 0.1,
                    "contributed": contributed,
                    "proposed_high": contributed,
                }
            ],
        }
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = run_agreement(a_path=a_path, b_path=b_path, out_dir=tmp_path / "out")

    assert (tmp_path / "out" / "agreement.json").exists()
    assert (tmp_path / "out" / "consensus_labels.jsonl").exists()
    rho = result["report"]["rank"]["mean_spearman_within_trajectory"]
    assert math.isnan(rho), "a single action has no within-trajectory rank"
    consensus = json.loads(
        (tmp_path / "out" / "consensus_labels.jsonl").read_text().strip()
    )
    assert consensus["steps"][0]["proposed_high"] is False
