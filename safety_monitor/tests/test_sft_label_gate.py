"""SFT gate: refuse unless labels beat the sink oracle and the task probe."""

from __future__ import annotations

from pathlib import Path

from safety_monitor.analysis.credit_assignment import (
    CreditAssignment,
    ProposedAction,
    assign_all,
    fixture_no_credit_completer,
    make_copy_oracle_completer,
    make_marked_ids_completer,
)
from safety_monitor.analysis.stats import auroc
from safety_monitor.online.baselines import TaskIdentityProbe
from safety_monitor.sft.experiment import run_experiment
from safety_monitor.sft.label_gate import (
    evaluate_sft_gate,
    maybe_launch_sft,
    refuse_sft_if_closed,
    run_sft_gate,
)
from safety_monitor.sft.scaling import run_scaling_ladder
from safety_monitor.synthesis.pairs import LabeledStep, MinedTrajectory
from safety_monitor.types import SafetyLabel


def _step(
    action_id: str,
    command: str,
    *,
    sink: str | None = None,
    label: SafetyLabel = SafetyLabel.LOW_UNSAFE,
    label_source: str = "evaluator",
) -> LabeledStep:
    return LabeledStep(
        action_id=action_id,
        tool_name="terminal",
        arguments={"command": command},
        summary=command[:40],
        label=label,
        sink=sink,
        label_source=label_source,
    )


def _traj(
    instance_id: str,
    role: str,
    steps: list[LabeledStep],
    *,
    key: str | None = None,
    condition: str = "synthetic",
    run: str = "v4_synthetic",
    instruction: str = "Do the shared workplace task.",
) -> MinedTrajectory:
    rule_based = 1 if role == "harmful" else 0
    return MinedTrajectory(
        key=key or f"{run}::{instance_id}::{role}",
        instance_id=instance_id,
        run=run,
        condition=condition,
        actor="test",
        role=role,
        rule_based=rule_based,
        instruction=instruction,
        steps=steps,
    )


def _copy_oracle_corpus() -> list[MinedTrajectory]:
    trajs: list[MinedTrajectory] = []
    for i in range(6):
        iid = f"task-{i}"
        trajs.append(
            _traj(
                iid,
                "harmful",
                [
                    _step(f"{iid}-h0", "ls /workspace"),
                    _step(
                        f"{iid}-h1",
                        "git push origin main",
                        sink="vcs",
                        label=SafetyLabel.HIGH_UNSAFE,
                        label_source="evaluator_sink",
                    ),
                ],
            )
        )
        trajs.append(
            _traj(
                iid,
                "clean",
                [
                    _step(f"{iid}-c0", "ls /workspace"),
                    _step(f"{iid}-c1", "echo refuse"),
                ],
            )
        )
    return trajs


def _divergent_action_signal_corpus() -> tuple[list[MinedTrajectory], set[str]]:
    """Same task ids appear as both safe and unsafe; labels track the outcome.

    The live sink oracle would mark the sink step on unsafe traces. These
    proposed labels mark a *non-sink* step instead, so they are not a copy,
    and the action-level max beats an in-sample task-identity probe.
    """
    trajs: list[MinedTrajectory] = []
    high_ids: set[str] = set()
    for i in range(40):
        iid = f"mixed-{i}"
        marked = f"{iid}-pivot"
        high_ids.add(marked)
        trajs.append(
            _traj(
                iid,
                "harmful",
                [
                    _step(marked, "echo pivot-only"),
                    _step(f"{iid}-sink", "write /workspace/out", sink="write"),
                ],
            )
        )
        trajs.append(
            _traj(
                iid,
                "clean",
                [
                    _step(f"{iid}-safe0", "echo pivot-only"),
                    _step(f"{iid}-safe1", "write /workspace/out", sink="write"),
                ],
            )
        )
    return trajs, high_ids


def _unique_task_id_corpus() -> tuple[list[MinedTrajectory], set[str]]:
    """Every trajectory has its own task id, and instructions are identical.

    This is the shape of a real small sample: 25 distinct MG tasks, each run
    once. Nothing in the task text predicts the outcome, but the probe's
    ``task:<instance_id>`` feature is unique per row, so an in-sample fit
    memorises the labels and scores AUROC 1.0.
    """
    trajs: list[MinedTrajectory] = []
    high_ids: set[str] = set()
    for i in range(40):
        iid = f"uniq-harmful-{i}"
        marked = f"{iid}-pivot"
        high_ids.add(marked)
        trajs.append(
            _traj(
                iid,
                "harmful",
                [
                    _step(marked, "echo pivot-only"),
                    _step(f"{iid}-sink", "write /workspace/out", sink="write"),
                ],
            )
        )
        safe_iid = f"uniq-clean-{i}"
        trajs.append(
            _traj(
                safe_iid,
                "clean",
                [
                    _step(f"{safe_iid}-0", "echo pivot-only"),
                    _step(f"{safe_iid}-sink", "write /workspace/out", sink="write"),
                ],
            )
        )
    return trajs, high_ids


def _force_high_on_every_step(
    trajs: list[MinedTrajectory],
) -> list[CreditAssignment]:
    return [
        CreditAssignment(
            key=traj.key,
            instance_id=traj.instance_id,
            rule_based=int(traj.rule_based or 0),
            proposed=[
                ProposedAction(
                    action_id=step.action_id,
                    credit=1.0,
                    contributed=True,
                    proposed_high=True,
                    rationale="test_all_positive",
                )
                for step in traj.steps
            ],
            completer_id="test_all_positive",
            real_llm=True,
        )
        for traj in trajs
    ]


def test_gate_closed_when_labels_copy_the_sink_oracle() -> None:
    trajs = _copy_oracle_corpus()
    assigned = assign_all(trajs, make_copy_oracle_completer(trajs), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is False
    copy_cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert copy_cond.passed is False
    assert "copy of the sink oracle" in copy_cond.reason
    assert "copy of the sink oracle" in decision.reason
    assert decision.sft_allowed is False


def test_all_negative_labeler_fails_as_degenerate() -> None:
    trajs = _copy_oracle_corpus()
    assigned = assign_all(trajs, fixture_no_credit_completer, real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is False
    cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert cond.passed is False
    assert "labeled no positives" in cond.reason
    assert "all-negative" in cond.reason
    assert "labeled no positives" in decision.reason
    assert "copy of the sink oracle" not in decision.reason


def test_all_positive_labeler_fails_as_degenerate() -> None:
    trajs = _copy_oracle_corpus()
    assigned = _force_high_on_every_step(trajs)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is False
    cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert cond.passed is False
    assert "labeled no negatives" in cond.reason
    assert "all-positive" in cond.reason
    assert "labeled no negatives" in decision.reason
    assert "copy of the sink oracle" not in decision.reason


def test_exact_sink_oracle_copy_fails_with_copy_reason() -> None:
    trajs = _copy_oracle_corpus()
    assigned = assign_all(trajs, make_copy_oracle_completer(trajs), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is False
    cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert cond.passed is False
    assert "copy of the sink oracle" in cond.reason
    assert cond.details["vs_oracle"]["confusion"]["fp"] == 0
    assert cond.details["vs_oracle"]["confusion"]["fn"] == 0
    assert "labeled no positives" not in decision.reason


def test_two_way_disagreement_plus_action_auroc_opens_gate() -> None:
    trajs, high_ids = _divergent_action_signal_corpus()
    assigned = assign_all(trajs, make_marked_ids_completer(high_ids), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is True
    assert decision.sft_allowed is True
    assert all(c.passed for c in decision.conditions)
    copy_cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert copy_cond.details["two_way_disagreement"] is True
    assert copy_cond.details["vs_oracle"]["confusion"]["fp"] > 0
    assert copy_cond.details["vs_oracle"]["confusion"]["fn"] > 0
    assert copy_cond.details["n_positive_predictions"] >= 1
    assert copy_cond.details["n_negative_predictions"] >= 1


def test_gate_closed_when_action_signal_is_flat() -> None:
    trajs = _copy_oracle_corpus()
    assigned = assign_all(trajs, fixture_no_credit_completer, real_llm=False)
    decision = evaluate_sft_gate(trajs, assignments=assigned)
    assert decision.open is False
    assert decision.sft_launched is False
    copy_cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert copy_cond.passed is False
    assert "labeled no positives" in decision.reason


def test_gate_opens_when_labels_disagree_and_beat_task_identity() -> None:
    trajs, high_ids = _divergent_action_signal_corpus()
    assigned = assign_all(trajs, make_marked_ids_completer(high_ids), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    assert decision.open is True
    assert decision.sft_allowed is True
    assert all(c.passed for c in decision.conditions)
    copy_cond = next(
        c for c in decision.conditions if c.name == "disagrees_with_sink_oracle"
    )
    assert copy_cond.details["f1_vs_oracle"] < 0.95
    assert copy_cond.details["disagreement_rate"] >= 0.02


def test_in_sample_probe_memorises_unique_task_ids() -> None:
    """Pins the leakage that made gate condition two unpassable at small n."""
    trajs, _ = _unique_task_id_corpus()
    y = [1 if t.rule_based == 1 else 0 for t in trajs]

    in_sample = TaskIdentityProbe()
    in_sample.fit(trajs)
    memorised = auroc([in_sample.score(t)[0] for t in trajs], y)
    assert memorised == 1.0, "in-sample probe should separate perfectly here"

    held_out = auroc(TaskIdentityProbe.out_of_fold_scores(trajs), y)
    assert held_out < 1.0, "out-of-fold probe must not see its own task id"


def test_probe_condition_survives_task_id_memorisation() -> None:
    """Unique task ids must not close the gate on labels that carry signal."""
    trajs, high_ids = _unique_task_id_corpus()
    assigned = assign_all(trajs, make_marked_ids_completer(high_ids), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)

    probe_cond = next(c for c in decision.conditions if c.name == "beats_task_identity")
    assert probe_cond.details["task_identity_auroc_in_sample"] == 1.0
    assert probe_cond.details["task_identity_scoring"] == "out_of_fold_by_instance_id"
    assert probe_cond.passed is True
    assert decision.open is True


def test_small_sample_cannot_open_the_probe_condition() -> None:
    """A clean AUROC win on a handful of runs is not evidence."""
    trajs, high_ids = _unique_task_id_corpus()
    small = trajs[:20]
    assigned = assign_all(small, make_marked_ids_completer(high_ids), real_llm=True)
    decision = evaluate_sft_gate(small, assignments=assigned, real_labels=True)

    probe_cond = next(c for c in decision.conditions if c.name == "beats_task_identity")
    assert probe_cond.details["action_level_auroc"] == 1.0
    assert probe_cond.passed is False
    assert "sample too small" in probe_cond.reason
    assert decision.open is False


def test_fixture_labels_cannot_launch_even_if_numeric_gate_opens() -> None:
    trajs, high_ids = _divergent_action_signal_corpus()
    assigned = assign_all(trajs, make_marked_ids_completer(high_ids), real_llm=False)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=False)
    assert decision.open is True
    assert decision.sft_allowed is False
    maybe_launch_sft(decision, launch_if_open=True)
    assert decision.sft_launched is False


def test_default_path_never_launches() -> None:
    trajs, high_ids = _divergent_action_signal_corpus()
    assigned = assign_all(trajs, make_marked_ids_completer(high_ids), real_llm=True)
    decision = evaluate_sft_gate(trajs, assignments=assigned, real_labels=True)
    maybe_launch_sft(decision, launch_if_open=False)
    assert decision.sft_launched is False


def test_run_experiment_refuses_credit_labels_before_gpu(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("probe_hardware must not run when the gate is closed")

    monkeypatch.setattr("safety_monitor.sft.experiment.probe_hardware", boom)

    labeled = []
    for i in range(4):
        iid = f"safety-credit-{i:02d}"
        labeled.append(
            _traj(
                iid,
                "harmful",
                [
                    _step(
                        f"{iid}-0",
                        "ls /workspace",
                        label_source="fixture_credit",
                    ),
                    _step(
                        f"{iid}-1",
                        "git push origin main",
                        sink="vcs",
                        label=SafetyLabel.HIGH_UNSAFE,
                        label_source="fixture_credit",
                    ),
                ],
                condition="mg_passive",
                run="mg_baseline_test",
            )
        )
        labeled.append(
            _traj(
                iid,
                "clean",
                [
                    _step(f"{iid}-c0", "ls /workspace", label_source="fixture_credit"),
                    _step(f"{iid}-c1", "echo no", label_source="fixture_credit"),
                ],
                condition="mg_passive",
                run="mg_baseline_test",
            )
        )
    train = tmp_path / "credit_assignment" / "labeled_trajectories.jsonl"
    train.parent.mkdir(parents=True)
    train.write_text(
        "\n".join(t.model_dump_json() for t in labeled) + "\n", encoding="utf-8"
    )
    eval_path = tmp_path / "eval.jsonl"
    eval_path.write_text(labeled[0].model_dump_json() + "\n", encoding="utf-8")
    result = run_experiment(
        train_paths=[train],
        eval_path=eval_path,
        out_dir=tmp_path / "out",
        backend="mock",
    )
    assert result["sft_actually_ran"] is False
    assert result["sft_launched"] is False
    assert result["backend"] == "refused"
    assert result["gate"]["open"] is False
    assert (tmp_path / "out" / "gate.json").exists()


def test_run_scaling_refuses_credit_labels_before_gpu(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("probe_hardware must not run when the gate is closed")

    monkeypatch.setattr("safety_monitor.sft.scaling.probe_hardware", boom)

    labeled = [
        _traj(
            "safety-credit-00",
            "harmful",
            [
                _step(
                    "h0",
                    "git push origin main",
                    sink="vcs",
                    label=SafetyLabel.HIGH_UNSAFE,
                    label_source="fixture_credit",
                )
            ],
            condition="mg_passive",
            run="mg_baseline_test",
        ),
        _traj(
            "safety-credit-01",
            "clean",
            [_step("c0", "ls", label_source="fixture_credit")],
            condition="mg_passive",
            run="mg_baseline_test",
        ),
    ]
    train = tmp_path / "credit_assignment" / "labeled_trajectories.jsonl"
    train.parent.mkdir(parents=True)
    train.write_text(
        "\n".join(t.model_dump_json() for t in labeled) + "\n", encoding="utf-8"
    )
    eval_path = tmp_path / "eval.jsonl"
    eval_path.write_text(labeled[1].model_dump_json() + "\n", encoding="utf-8")
    result = run_scaling_ladder(
        train_paths=[train],
        eval_path=eval_path,
        out_dir=tmp_path / "out",
        backend="mock",
    )
    assert result["sft_actually_ran"] is False
    assert result["sft_launched"] is False
    assert result["backend"] == "refused"


def test_refuse_helper_is_none_for_ordinary_synthetic() -> None:
    trajs = _copy_oracle_corpus()
    assert refuse_sft_if_closed(trajs, ["synthetic_pairs/trajectories.jsonl"]) is None


def test_run_sft_gate_writes_closed_decision(tmp_path: Path) -> None:
    src = tmp_path / "trajs.jsonl"
    labels = tmp_path / "proposed_labels.jsonl"
    trajs = _copy_oracle_corpus()
    assigned = assign_all(trajs, make_copy_oracle_completer(trajs))
    src.write_text(
        "\n".join(t.model_dump_json() for t in trajs) + "\n", encoding="utf-8"
    )
    import json

    from safety_monitor.analysis.credit_assignment import assignment_to_record

    labels.write_text(
        "\n".join(json.dumps(assignment_to_record(a)) for a in assigned) + "\n",
        encoding="utf-8",
    )
    payload = run_sft_gate(
        trajectories_path=src,
        labels_path=labels,
        out_dir=tmp_path,
        launch_if_open=False,
    )
    assert payload["open"] is False
    assert payload["sft_launched"] is False
    assert payload["sft_allowed"] is False
    assert (tmp_path / "gate.json").exists()
