"""Orchestrate before/after critic evaluation for local Qwen SFT (or mock smoke)."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from safety_monitor.critic.base import SafetyCritic
from safety_monitor.sft.backends import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_EVAL_BATCH_SIZE,
    DEFAULT_GRAD_ACCUM,
    handle_hf_probe,
    hf_sft_ready,
    make_hf_completer,
    make_keyword_critic,
    make_logreg_critic,
    probe_hardware,
    train_lora_sft,
    train_mock_sft,
)
from safety_monitor.sft.data import (
    SFTExample,
    assert_train_is_synthetic,
    build_examples,
    count_from_trajectories,
    count_labels,
    load_synthetic_and_eval,
    select_few_shot,
    source_for_traj,
    split_synthetic_tasks,
    upsample_high,
)
from safety_monitor.sft.metrics import (
    action_metrics,
    binary_from_label,
    trajectory_metrics,
)
from safety_monitor.sft.report import write_report
from safety_monitor.synthesis.pairs import MinedTrajectory
from safety_monitor.types import Trajectory


def _traj_as_framework(traj: MinedTrajectory) -> Trajectory:
    from safety_monitor.sft.data import mined_to_steps

    return Trajectory(
        instance_id=traj.instance_id,
        steps=mined_to_steps(traj),
        instruction=traj.instruction,
        metadata={"actor": traj.actor, "key": traj.key, "role": traj.role},
    )


def evaluate_critic(
    critic: SafetyCritic,
    trajs: Sequence[MinedTrajectory],
    *,
    eval_name: str,
) -> dict[str, Any]:
    labelled = []
    y_true: list[int] = []
    y_pred: list[int] = []
    roles: list[str] = []
    rule_based: list[int] = []
    for traj in trajs:
        wrapped = _traj_as_framework(traj)
        labels = critic.label_trajectory(wrapped)
        labels.instance_id = traj.key
        labelled.append(labels)
        gold = [binary_from_label(step.label) for step in traj.steps]
        pred = [binary_from_label(v.label) for v in labels.verdicts]
        if len(gold) != len(pred):
            raise RuntimeError(
                f"Verdict count {len(pred)} != labeled steps {len(gold)} for {traj.key}"
            )
        y_true.extend(gold)
        y_pred.extend(pred)
        roles.append(traj.role)
        rule_based.append(int(traj.rule_based or 0))
    return {
        "eval_name": eval_name,
        "critic_id": critic.critic_id,
        "action": action_metrics(y_true, y_pred),
        "trajectory": trajectory_metrics(
            labelled,
            rule_based=rule_based,
            roles=roles,
            critic_id=critic.critic_id,
        ),
    }


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def run_experiment(
    *,
    train_paths: Sequence[str | Path],
    eval_path: str | Path,
    out_dir: str | Path,
    holdout_fraction: float = 0.2,
    backend: str = "auto",
    model_path: str | None = None,
    max_v3_trajectories: int | None = None,
    upsample: bool = True,
    epochs: int = 2,
    use_shipped_splits: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    grad_accum: int = DEFAULT_GRAD_ACCUM,
    eval_batch_size: int = DEFAULT_EVAL_BATCH_SIZE,
    strict: bool = False,
    command: str = "sft-run",
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    probe = probe_hardware(model_path=model_path, backend=backend)
    _dump(out / "probe.json", probe.as_dict())
    handle_hf_probe(
        probe,
        requested_backend=backend,
        strict=strict,
        allow_mock_fallback=True,
    )

    run_config = {
        "command": command,
        "train_paths": [str(p) for p in train_paths],
        "eval_path": str(eval_path),
        "out_dir": str(out),
        "backend": backend,
        "requested_backend": backend,
        "model_path": model_path,
        "epochs": epochs,
        "batch_size": int(batch_size),
        "grad_accum": int(grad_accum),
        "eval_batch_size": int(eval_batch_size),
        "holdout_fraction": holdout_fraction,
        "max_v3_trajectories": max_v3_trajectories,
        "use_shipped_splits": bool(use_shipped_splits),
        "strict": bool(strict),
    }
    _dump(out / "run_config.json", run_config)

    synthetic, v3 = load_synthetic_and_eval(train_paths, eval_path)
    assert_train_is_synthetic(synthetic)
    if max_v3_trajectories is not None:
        v3 = v3[: max(0, max_v3_trajectories)]

    if use_shipped_splits:
        from safety_monitor.sft.data import (
            assert_train_eval_firewall,
            split_by_shipped_fields,
        )

        shipped = split_by_shipped_fields(synthetic, v3)
        overlap = assert_train_eval_firewall(
            shipped.train_instance_ids,
            [t.instance_id for t in shipped.v3_eval_trajectories],
        )
        print(
            f"firewall check: train ∩ v3-eval instance_ids == {overlap}",
            file=sys.stderr,
        )
        split = shipped.as_legacy_split()
    else:
        split = split_synthetic_tasks(synthetic, v3, holdout_fraction=holdout_fraction)
    _dump(out / "split.json", split.as_dict())

    train_examples = build_examples(
        split.train_trajectories,
        bucket="train",
        source="synthetic",
    )
    for example, traj in _align_sources(train_examples, split.train_trajectories):
        example.source = source_for_traj(traj)

    counts = {
        "train": count_labels(train_examples),
        "synthetic_holdout": count_from_trajectories(split.holdout_trajectories),
        "v3_eval": count_from_trajectories(split.v3_eval_trajectories),
    }
    _dump(out / "example_counts.json", counts)

    few_shot = select_few_shot(train_examples)
    _dump(
        out / "few_shot.json",
        {
            "n_turns": len(few_shot),
            "from_train_only": True,
            "note": "Few-shot demos are train-bucket actions; never holdout or v3.",
        },
    )

    sft_actually_ran = False
    train_stats: dict[str, Any]
    after_critic: SafetyCritic
    before_zero: SafetyCritic
    before_few: SafetyCritic

    if hf_sft_ready(probe) and probe.backend == "hf":
        from safety_monitor.critic.prompted import PromptedSafetyCritic

        assert probe.model_path is not None
        sft_messages = [
            e.messages
            for e in (upsample_high(train_examples) if upsample else train_examples)
        ]
        train_stats = train_lora_sft(
            probe.model_path,
            sft_messages,
            out / "hf",
            epochs=epochs,
            batch_size=batch_size,
            grad_accum=grad_accum,
        )
        sft_actually_ran = True
        zero_complete = make_hf_completer(probe.model_path)
        after_complete = make_hf_completer(
            probe.model_path, adapter_dir=train_stats["adapter_dir"]
        )
        before_zero = PromptedSafetyCritic(zero_complete, critic_id="qwen-zero-shot")
        before_few = PromptedSafetyCritic(
            zero_complete, critic_id="qwen-few-shot", few_shot=few_shot
        )
        after_critic = PromptedSafetyCritic(after_complete, critic_id="qwen-lora-sft")
    else:
        fit_examples = upsample_high(train_examples) if upsample else train_examples
        model, train_stats = train_mock_sft(
            [e.user_text for e in fit_examples],
            [binary_from_label(e.label) for e in fit_examples],
        )
        train_stats["sft_actually_ran"] = False
        train_stats["note"] = probe.reason
        train_stats["batch_size"] = int(batch_size)
        train_stats["grad_accum"] = int(grad_accum)
        train_stats["eval_batch_size"] = int(eval_batch_size)
        before_zero = make_keyword_critic(critic_id="mock-keyword-zero-shot")
        before_few = make_keyword_critic(
            critic_id="mock-keyword-few-shot", few_shot=few_shot
        )
        after_critic = make_logreg_critic(model, critic_id="mock-logreg-sft")

    _dump(out / "train_stats.json", train_stats)

    stages = {
        "before_zero_shot": before_zero,
        "before_few_shot": before_few,
        "after_sft": after_critic,
    }
    eval_sets = {
        "v3_eval": split.v3_eval_trajectories,
        "synthetic_holdout": split.holdout_trajectories,
        "synthetic_train": split.train_trajectories,
    }
    metrics: dict[str, Any] = {}
    for stage_name, critic in stages.items():
        metrics[stage_name] = {}
        for set_name, trajs in eval_sets.items():
            print(
                f"Evaluating {stage_name}/{set_name} ({len(trajs)} trajectories)",
                file=sys.stderr,
            )
            metrics[stage_name][set_name] = evaluate_critic(
                critic, trajs, eval_name=f"{stage_name}/{set_name}"
            )

    used_backend = "hf" if sft_actually_ran else "mock"
    result = {
        "sft_actually_ran": sft_actually_ran,
        "backend": used_backend,
        "requested_backend": backend,
        "run_config": run_config,
        "probe": probe.as_dict(),
        "split": split.as_dict(),
        "example_counts": counts,
        "train_stats": train_stats,
        "metrics": metrics,
        "firewall": {
            "train_sources": "synthetic v4/v5/v6 only",
            "v3_in_train": False,
            "holdout_is_task_level": True,
            "holdout_instance_overlap_with_train": [],
            "split_source": split.split_source,
        },
    }
    overlap = set(split.train_instance_ids) & set(split.holdout_instance_ids)
    result["firewall"]["holdout_instance_overlap_with_train"] = sorted(overlap)
    _dump(out / "metrics.json", result)
    write_report(out / "report.md", result)
    return result


def _align_sources(
    examples: list[SFTExample], trajs: Sequence[MinedTrajectory]
) -> list[tuple[SFTExample, MinedTrajectory]]:
    by_key = {t.key: t for t in trajs}
    paired: list[tuple[SFTExample, MinedTrajectory]] = []
    for example in examples:
        traj = by_key.get(example.trajectory_key)
        if traj is not None:
            paired.append((example, traj))
    return paired
