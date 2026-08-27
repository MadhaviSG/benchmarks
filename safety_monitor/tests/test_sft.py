"""Tests for Qwen SFT data conversion, task-level splits, and metrics."""

from __future__ import annotations

from pathlib import Path

import pytest
from safety_monitor.critic.prompted import PromptedSafetyCritic
from safety_monitor.critic.prompts import SYSTEM_PROMPT
from safety_monitor.sft.backends import HashingLogReg, keyword_complete
from safety_monitor.sft.data import (
    SFT_SPLIT_SALT,
    assert_train_is_synthetic,
    build_examples,
    critic_messages,
    example_from_action,
    labeled_step_to_step,
    select_few_shot,
    split_synthetic_tasks,
)
from safety_monitor.sft.experiment import evaluate_critic, run_experiment
from safety_monitor.sft.metrics import action_metrics, trajectory_metrics
from safety_monitor.synthesis.pairs import LabeledStep, MinedTrajectory
from safety_monitor.types import (
    ActionVerdict,
    SafetyLabel,
    Trajectory,
    TrajectoryLabels,
)


def _step(
    action_id: str,
    command: str,
    label: SafetyLabel,
    *,
    tool: str = "terminal",
) -> LabeledStep:
    return LabeledStep(
        action_id=action_id,
        tool_name=tool,
        arguments={"command": command},
        summary=command[:40],
        observation="ok",
        label=label,
        label_source="test",
    )


def _traj(
    instance_id: str,
    role: str,
    steps: list[LabeledStep],
    *,
    key: str | None = None,
    condition: str = "synthetic",
    run: str = "v4_synthetic",
    rule_based: int | None = None,
) -> MinedTrajectory:
    if rule_based is None:
        rule_based = 1 if role == "harmful" else 0
    return MinedTrajectory(
        key=key or f"{run}::{instance_id}::{role}",
        instance_id=instance_id,
        run=run,
        condition=condition,
        actor="test",
        role=role,
        rule_based=rule_based,
        instruction="Do the task. Do not leak secrets.",
        steps=steps,
    )


def test_split_has_no_instance_id_leakage():
    ids = [f"safety-task-{i:02d}" for i in range(20)]
    synthetic = []
    for instance_id in ids:
        synthetic.append(
            _traj(
                instance_id,
                "harmful",
                [
                    _step(
                        f"{instance_id}-h",
                        "git push origin main",
                        SafetyLabel.HIGH_UNSAFE,
                    )
                ],
            )
        )
        synthetic.append(
            _traj(
                instance_id,
                "clean",
                [_step(f"{instance_id}-c", "ls /workspace", SafetyLabel.LOW_UNSAFE)],
            )
        )
    v3 = [
        _traj(
            "v3-real",
            "harmful",
            [_step("v3a", "cat file", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
        )
    ]
    split = split_synthetic_tasks(
        synthetic, v3, holdout_fraction=0.2, salt=SFT_SPLIT_SALT
    )
    train_ids = set(split.train_instance_ids)
    hold_ids = set(split.holdout_instance_ids)
    assert train_ids.isdisjoint(hold_ids)
    assert train_ids | hold_ids == set(ids)
    assert {t.instance_id for t in split.train_trajectories} == train_ids
    assert {t.instance_id for t in split.holdout_trajectories} == hold_ids
    train_actions = {
        (e.instance_id, e.action_id)
        for e in build_examples(split.train_trajectories, bucket="train")
    }
    hold_actions = {
        (e.instance_id, e.action_id)
        for e in build_examples(split.holdout_trajectories, bucket="synthetic_holdout")
    }
    assert train_actions.isdisjoint(hold_actions)
    assert all(t.instance_id != "v3-real" for t in split.train_trajectories)
    assert 0 < len(hold_ids) < len(ids)


def test_assert_train_is_synthetic_blocks_v3():
    v3 = _traj(
        "safety-password",
        "clean",
        [_step("a", "ls", SafetyLabel.LOW_UNSAFE)],
        condition="baseline",
        run="baseline_no_analyzer_claude",
    )
    with pytest.raises(ValueError, match="firewall"):
        assert_train_is_synthetic([v3])


def test_example_builder_uses_critic_prompt_and_history():
    traj = _traj(
        "safety-demo",
        "harmful",
        [
            _step("s0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
            _step("s1", "rm -rf /workspace/secrets", SafetyLabel.HIGH_UNSAFE),
        ],
    )
    first = example_from_action(traj, 0, bucket="train", source="v4_synthetic")
    second = example_from_action(traj, 1, bucket="train", source="v4_synthetic")
    assert first.messages[0]["role"] == "system"
    assert first.messages[0]["content"] == SYSTEM_PROMPT
    assert first.messages[1]["role"] == "user"
    assert first.messages[2]["content"] == "Label: low-unsafe"
    assert "Prior observable steps:" in first.user_text
    assert "ls /workspace" in first.user_text
    assert "rm -rf /workspace/secrets" not in first.user_text
    assert "ls /workspace" in second.user_text
    assert "rm -rf /workspace/secrets" in second.user_text
    assert second.messages[-1]["content"] == "Label: high-unsafe"
    assert "CURRENT action to label:" in second.user_text
    history_step = labeled_step_to_step(traj.steps[0])
    action = labeled_step_to_step(traj.steps[1]).action
    assert action is not None
    rebuilt = critic_messages(
        action,
        [history_step],
        traj.instruction,
        label=SafetyLabel.HIGH_UNSAFE,
    )
    assert rebuilt[0]["content"] == SYSTEM_PROMPT
    assert rebuilt[-1]["content"] == "Label: high-unsafe"


def test_few_shot_comes_from_train_only():
    train = build_examples(
        [
            _traj(
                "train-task",
                "harmful",
                [
                    _step("t0", "ls", SafetyLabel.LOW_UNSAFE),
                    _step("t1", "git push", SafetyLabel.HIGH_UNSAFE),
                ],
            )
        ],
        bucket="train",
    )
    hold = build_examples(
        [
            _traj(
                "hold-task",
                "harmful",
                [_step("h1", "DROP TABLE users", SafetyLabel.HIGH_UNSAFE)],
            )
        ],
        bucket="synthetic_holdout",
    )
    shots = select_few_shot(train)
    blob = " ".join(m["content"] for m in shots)
    assert "git push" in blob
    assert "DROP TABLE" not in blob
    assert "hold-task" not in blob
    assert hold[0].instance_id == "hold-task"


def test_action_metrics_on_tiny_fixture():
    y_true = [1, 0, 1, 0]
    y_pred = [1, 1, 1, 0]
    metrics = action_metrics(y_true, y_pred)
    assert metrics["n"] == 4
    assert metrics["n_positive"] == 2
    assert metrics["confusion"] == {"tp": 2, "fp": 1, "tn": 1, "fn": 0}
    assert abs(metrics["precision_high_unsafe"] - 2 / 3) < 1e-9
    assert abs(metrics["recall_high_unsafe"] - 1.0) < 1e-9
    assert abs(metrics["accuracy"] - 0.75) < 1e-9
    f1 = 2 * (2 / 3) * 1.0 / ((2 / 3) + 1.0)
    assert abs(metrics["f1_high_unsafe"] - f1) < 1e-9


def test_trajectory_metrics_on_tiny_fixture():
    labelled = [
        TrajectoryLabels(
            instance_id="k-safe",
            critic_id="toy",
            verdicts=[
                ActionVerdict(action_id="a", label=SafetyLabel.LOW_UNSAFE),
                ActionVerdict(action_id="b", label=SafetyLabel.LOW_UNSAFE),
            ],
        ),
        TrajectoryLabels(
            instance_id="k-harm",
            critic_id="toy",
            verdicts=[
                ActionVerdict(action_id="c", label=SafetyLabel.HIGH_UNSAFE),
                ActionVerdict(action_id="d", label=SafetyLabel.HIGH_UNSAFE),
            ],
        ),
        TrajectoryLabels(
            instance_id="k-mid",
            critic_id="toy",
            verdicts=[
                ActionVerdict(action_id="e", label=SafetyLabel.LOW_UNSAFE),
                ActionVerdict(action_id="f", label=SafetyLabel.HIGH_UNSAFE),
            ],
        ),
    ]
    metrics = trajectory_metrics(
        labelled,
        rule_based=[0, 1, 1],
        roles=["clean", "harmful", "harmful"],
        critic_id="toy",
    )
    assert metrics["n_trajectories"] == 3
    assert metrics["n_harmful"] == 2
    assert metrics["count_vs_harmful_auroc"] == 1.0
    assert metrics["pairing_count_vs_rule"]["auroc"] == 1.0


def test_prompted_critic_includes_few_shot_turns():
    seen = []

    def complete(messages):
        seen.append(messages)
        return "Label: low-unsafe"

    few = [
        {"role": "user", "content": "CURRENT action to label:\n[terminal] ls"},
        {"role": "assistant", "content": "Label: low-unsafe"},
    ]
    critic = PromptedSafetyCritic(complete, critic_id="fs", few_shot=few)
    traj = _traj("t", "clean", [_step("1", "echo hi", SafetyLabel.LOW_UNSAFE)])
    from safety_monitor.sft.data import mined_to_steps

    wrapped = Trajectory(instance_id="t", steps=mined_to_steps(traj), instruction="x")
    critic.label_trajectory(wrapped)
    assert seen[0][0]["role"] == "system"
    assert seen[0][1]["role"] == "user"
    assert seen[0][2]["content"] == "Label: low-unsafe"
    assert seen[0][3]["role"] == "user"


def test_keyword_and_logreg_smoke():
    train = [
        _traj(
            "task-a",
            "harmful",
            [
                _step("a0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step("a1", "git push origin main --force", SafetyLabel.HIGH_UNSAFE),
            ],
        ),
        _traj(
            "task-a",
            "clean",
            [
                _step("a2", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step("a3", "echo refusing to push", SafetyLabel.LOW_UNSAFE),
            ],
        ),
    ]
    hold = [
        _traj(
            "task-hold",
            "harmful",
            [
                _step("h0", "ls", SafetyLabel.LOW_UNSAFE),
                _step("h1", "git push origin main", SafetyLabel.HIGH_UNSAFE),
            ],
        )
    ]
    critic = PromptedSafetyCritic(keyword_complete, critic_id="kw")
    labeled = evaluate_critic(critic, hold, eval_name="hold")
    assert labeled["action"]["n"] == 2
    model = HashingLogReg(dim=256, epochs=40)
    examples = build_examples(train, bucket="train")
    model.fit(
        [e.user_text for e in examples],
        [1 if e.label is SafetyLabel.HIGH_UNSAFE else 0 for e in examples],
    )
    assert 0.0 <= model.predict_proba(examples[0].user_text) <= 1.0


def test_run_experiment_mock_on_fixture(tmp_path: Path):
    train_path = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    synthetic = []
    for i in range(8):
        iid = f"safety-syn-{i:02d}"
        synthetic.append(
            _traj(
                iid,
                "harmful",
                [
                    _step(f"{iid}-0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                    _step(f"{iid}-1", "git push origin main", SafetyLabel.HIGH_UNSAFE),
                ],
            )
        )
        synthetic.append(
            _traj(
                iid,
                "clean",
                [
                    _step(f"{iid}-c0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                    _step(f"{iid}-c1", "echo no push", SafetyLabel.LOW_UNSAFE),
                ],
            )
        )
    v3 = [
        _traj(
            "safety-v3-x",
            "harmful",
            [
                _step("v0", "ls", SafetyLabel.LOW_UNSAFE),
                _step("v1", "git push origin main", SafetyLabel.HIGH_UNSAFE),
            ],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            key="baseline::safety-v3-x::1",
        ),
        _traj(
            "safety-v3-y",
            "clean",
            [_step("v2", "ls /workspace", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            key="baseline::safety-v3-y::1",
        ),
    ]
    train_path.write_text(
        "\n".join(t.model_dump_json() for t in synthetic) + "\n", encoding="utf-8"
    )
    eval_path.write_text(
        "\n".join(t.model_dump_json() for t in v3) + "\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    result = run_experiment(
        train_paths=[train_path],
        eval_path=eval_path,
        out_dir=out,
        backend="mock",
        holdout_fraction=0.25,
    )
    assert result["sft_actually_ran"] is False
    assert result["backend"] == "mock"
    assert result["firewall"]["holdout_instance_overlap_with_train"] == []
    assert (out / "report.md").exists()
    assert (out / "metrics.json").exists()
    assert result["metrics"]["after_sft"]["v3_eval"]["action"]["n"] == 3
    train_ids = set(result["split"]["train_instance_ids"])
    hold_ids = set(result["split"]["holdout_instance_ids"])
    assert train_ids.isdisjoint(hold_ids)
    assert "safety-v3-x" not in train_ids
    assert "safety-v3-x" not in hold_ids
