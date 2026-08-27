"""Convert labeled trajectories into critic SFT examples and task-level splits.

The train/eval firewall is enforced here: training examples may only come from
synthetic (v4/v5/v6) trajectories. Splits are by ``instance_id`` so no task's
steps appear in both train and the synthetic holdout.

The scaling ladder uses the corpus's shipped task-level ``split`` field
(salt ``v6-synthetic-500``): train / dev / test. The hashed ``qwen-sft``
cut remains available for the original single-run experiment.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from safety_monitor.critic.prompted import format_action, format_history
from safety_monitor.critic.prompts import SYSTEM_PROMPT, render_history
from safety_monitor.critic.shieldgemma import format_shieldgemma_prompt
from safety_monitor.synthesis.pairs import MinedTrajectory, assign_split
from safety_monitor.synthesis.v6_splits import SPLIT_SALT as SHIPPED_SPLIT_SALT
from safety_monitor.types import (
    ObservableAction,
    Observation,
    SafetyLabel,
    Step,
)


SFT_SPLIT_SALT = "qwen-sft"
DEFAULT_HOLDOUT_FRACTION = 0.2
SYNTHETIC_CONDITIONS = frozenset({"synthetic"})
SYNTHETIC_CORPORA = frozenset({"v4", "v5", "v6"})
PROMPT_FORMAT_QWEN = "qwen"
PROMPT_FORMAT_SHIELDGEMMA = "shieldgemma"


@dataclass
class SFTExample:
    """One (history → current action) prompt paired with a binary label."""

    example_id: str
    instance_id: str
    trajectory_key: str
    action_id: str
    label: SafetyLabel
    messages: list[dict[str, str]]
    role: str
    rule_based: int
    source: str
    bucket: str  # train | synthetic_holdout | v3_eval | synthetic_dev | synthetic_test
    prompt_format: str = PROMPT_FORMAT_QWEN

    @property
    def user_text(self) -> str:
        for msg in self.messages:
            if msg["role"] == "user":
                return msg["content"]
        return ""

    @property
    def assistant_text(self) -> str:
        for msg in reversed(self.messages):
            if msg["role"] == "assistant":
                return msg["content"]
        if self.prompt_format == PROMPT_FORMAT_SHIELDGEMMA:
            return shieldgemma_assistant_text(self.label)
        return f"Label: {self.label.value}"


@dataclass
class SplitManifest:
    """Task-level split of synthetic data plus the v3 eval corpus."""

    train_instance_ids: list[str]
    holdout_instance_ids: list[str]
    train_trajectories: list[MinedTrajectory]
    holdout_trajectories: list[MinedTrajectory]
    v3_eval_trajectories: list[MinedTrajectory]
    salt: str = SFT_SPLIT_SALT
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION
    split_source: str = "hashed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "salt": self.salt,
            "split_source": self.split_source,
            "holdout_fraction": self.holdout_fraction,
            "n_train_tasks": len(self.train_instance_ids),
            "n_holdout_tasks": len(self.holdout_instance_ids),
            "n_train_trajectories": len(self.train_trajectories),
            "n_holdout_trajectories": len(self.holdout_trajectories),
            "n_v3_eval_trajectories": len(self.v3_eval_trajectories),
            "train_instance_ids": sorted(self.train_instance_ids),
            "holdout_instance_ids": sorted(self.holdout_instance_ids),
            "n_v3_eval_tasks": len({t.instance_id for t in self.v3_eval_trajectories}),
        }


@dataclass
class ShippedSplitManifest:
    """Train/dev/test partition taken from the corpus ``split`` field."""

    train_instance_ids: list[str]
    dev_instance_ids: list[str]
    test_instance_ids: list[str]
    unused_instance_ids: list[str]
    train_trajectories: list[MinedTrajectory]
    dev_trajectories: list[MinedTrajectory]
    test_trajectories: list[MinedTrajectory]
    v3_eval_trajectories: list[MinedTrajectory]
    salt: str = SHIPPED_SPLIT_SALT
    train_v3_overlap: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        overlap = self.train_v3_overlap
        if overlap is None:
            overlap = sorted(
                set(self.train_instance_ids)
                & {t.instance_id for t in self.v3_eval_trajectories}
            )
        return {
            "salt": self.salt,
            "split_source": "shipped",
            "n_train_tasks": len(self.train_instance_ids),
            "n_dev_tasks": len(self.dev_instance_ids),
            "n_test_tasks": len(self.test_instance_ids),
            "n_unused_tasks": len(self.unused_instance_ids),
            "n_train_trajectories": len(self.train_trajectories),
            "n_dev_trajectories": len(self.dev_trajectories),
            "n_test_trajectories": len(self.test_trajectories),
            "n_v3_eval_trajectories": len(self.v3_eval_trajectories),
            "n_v3_eval_tasks": len({t.instance_id for t in self.v3_eval_trajectories}),
            "train_instance_ids": sorted(self.train_instance_ids),
            "dev_instance_ids": sorted(self.dev_instance_ids),
            "test_instance_ids": sorted(self.test_instance_ids),
            "unused_instance_ids": sorted(self.unused_instance_ids),
            "firewall": {
                "train_intersect_v3_eval_instance_ids": overlap,
                "ok": overlap == [],
            },
        }

    def as_legacy_split(self) -> SplitManifest:
        """Map test → holdout so the original single-run experiment can consume it."""
        return SplitManifest(
            train_instance_ids=list(self.train_instance_ids),
            holdout_instance_ids=list(self.test_instance_ids),
            train_trajectories=list(self.train_trajectories),
            holdout_trajectories=list(self.test_trajectories),
            v3_eval_trajectories=list(self.v3_eval_trajectories),
            salt=self.salt,
            holdout_fraction=0.0,
            split_source="shipped",
        )


def load_trajectories(path: str | Path) -> list[MinedTrajectory]:
    trajs: list[MinedTrajectory] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            trajs.append(MinedTrajectory.model_validate(json.loads(line)))
    return trajs


def is_synthetic(traj: MinedTrajectory) -> bool:
    if traj.corpus in SYNTHETIC_CORPORA:
        return True
    if traj.condition in SYNTHETIC_CONDITIONS:
        return True
    run = (traj.run or "").lower()
    return (
        "synthetic" in run
        or run.startswith("v4_")
        or run.startswith("v5_")
        or run.startswith("v6_")
    )


def assert_train_is_synthetic(trajs: Sequence[MinedTrajectory]) -> None:
    """Refuse to put real OAS v3 runs on the train side of the firewall."""
    leaked = [t for t in trajs if not is_synthetic(t)]
    if leaked:
        sample = leaked[0]
        raise ValueError(
            "Train/eval firewall: refusing to train on non-synthetic trajectories. "
            f"Example key={sample.key!r} run={sample.run!r} condition={sample.condition!r}. "
            "Train must be v4/v5/v6 synthetic pairs only."
        )


def assert_train_eval_firewall(
    train_instance_ids: Sequence[str],
    eval_instance_ids: Sequence[str],
) -> list[str]:
    """Assert ``train ∩ v3-eval instance_ids == []``. Returns the overlap (empty)."""
    overlap = sorted(set(train_instance_ids) & set(eval_instance_ids))
    if overlap:
        sample = overlap[:10]
        raise ValueError(
            "Train/eval firewall: train ∩ v3-eval instance_ids is not empty: "
            f"{sample}{'…' if len(overlap) > 10 else ''} "
            f"(n={len(overlap)}). Training must never see real OAS eval tasks."
        )
    return overlap


def labeled_step_to_step(step: Any) -> Step:
    return Step(
        action=ObservableAction(
            action_id=step.action_id,
            tool_name=step.tool_name,
            arguments=step.arguments or {},
            summary=step.summary,
        ),
        observation=Observation(
            text=step.observation or "", is_error=bool(step.is_error)
        ),
    )


def mined_to_steps(traj: MinedTrajectory) -> list[Step]:
    return [labeled_step_to_step(step) for step in traj.steps]


def critic_user_prompt(
    action: ObservableAction,
    history: Sequence[Step],
    instruction: str | None,
    *,
    max_history_steps: int = 20,
) -> str:
    """Same user prompt the prompted critic uses at eval."""
    return render_history(
        format_history(history, max_steps=max_history_steps),
        format_action(action),
        instruction,
    )


def critic_messages(
    action: ObservableAction,
    history: Sequence[Step],
    instruction: str | None,
    *,
    label: SafetyLabel | None = None,
    few_shot: Sequence[dict[str, str]] | None = None,
    max_history_steps: int = 20,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if few_shot:
        messages.extend(few_shot)
    messages.append(
        {
            "role": "user",
            "content": critic_user_prompt(
                action, history, instruction, max_history_steps=max_history_steps
            ),
        }
    )
    if label is not None:
        messages.append({"role": "assistant", "content": f"Label: {label.value}"})
    return messages


def shieldgemma_assistant_text(label: SafetyLabel) -> str:
    """ShieldGemma SFT target: Yes = policy violation = high-unsafe."""
    return "Yes" if label is SafetyLabel.HIGH_UNSAFE else "No"


def shieldgemma_sft_messages(
    action: ObservableAction,
    history: Sequence[Step],
    *,
    label: SafetyLabel | None = None,
    max_history_steps: int = 8,
) -> list[dict[str, str]]:
    """Render one action into ShieldGemma's native Yes/No policy-prompt format.

    Uses the same ``_POLICY`` + template as :func:`format_shieldgemma_prompt`
    so zero-shot and post-SFT scoring stay a controlled comparison.
    """
    prompt = format_shieldgemma_prompt(
        action, history, max_history_steps=max_history_steps
    )
    messages = [{"role": "user", "content": prompt}]
    if label is not None:
        messages.append(
            {"role": "assistant", "content": shieldgemma_assistant_text(label)}
        )
    return messages


def format_example_as_shieldgemma(example: SFTExample) -> list[dict[str, str]]:
    """Return ShieldGemma-format messages for an example already built that way.

    Build examples with ``prompt_format='shieldgemma'`` (see
    :func:`example_from_action`). Generic Qwen-format examples cannot be
    losslessly converted without the original action/history objects.
    """
    if example.prompt_format != PROMPT_FORMAT_SHIELDGEMMA:
        raise ValueError(
            "format_example_as_shieldgemma requires prompt_format='shieldgemma'; "
            "rebuild via example_from_action(..., prompt_format='shieldgemma')"
        )
    return list(example.messages)


def example_from_action(
    traj: MinedTrajectory,
    step_index: int,
    *,
    bucket: str,
    source: str,
    max_history_steps: int = 20,
    prompt_format: str = PROMPT_FORMAT_QWEN,
) -> SFTExample:
    steps = mined_to_steps(traj)
    labeled = traj.steps[step_index]
    action = steps[step_index].action
    if action is None:
        raise ValueError(f"Missing action at {traj.key}:{step_index}")
    history = steps[:step_index]
    if prompt_format == PROMPT_FORMAT_SHIELDGEMMA:
        messages = shieldgemma_sft_messages(
            action,
            history,
            label=labeled.label,
            max_history_steps=min(max_history_steps, 8),
        )
    elif prompt_format == PROMPT_FORMAT_QWEN:
        messages = critic_messages(
            action,
            history,
            traj.instruction,
            label=labeled.label,
            max_history_steps=max_history_steps,
        )
    else:
        raise ValueError(f"Unknown prompt_format {prompt_format!r}")
    return SFTExample(
        example_id=f"{traj.key}:{labeled.action_id}",
        instance_id=traj.instance_id,
        trajectory_key=traj.key,
        action_id=labeled.action_id,
        label=labeled.label,
        messages=messages,
        role=traj.role,
        rule_based=int(traj.rule_based or 0),
        source=source,
        bucket=bucket,
        prompt_format=prompt_format,
    )


def build_examples(
    trajs: Sequence[MinedTrajectory],
    *,
    bucket: str,
    source: str = "unknown",
    max_history_steps: int = 20,
    prompt_format: str = PROMPT_FORMAT_QWEN,
) -> list[SFTExample]:
    examples: list[SFTExample] = []
    for traj in trajs:
        for i, step in enumerate(traj.steps):
            if not step.action_id:
                continue
            examples.append(
                example_from_action(
                    traj,
                    i,
                    bucket=bucket,
                    source=source,
                    max_history_steps=max_history_steps,
                    prompt_format=prompt_format,
                )
            )
    return examples


def source_for_traj(traj: MinedTrajectory) -> str:
    if traj.corpus:
        return f"{traj.corpus}_synthetic"
    run = (traj.run or "").lower()
    if "v6" in run:
        return "v6_synthetic"
    if "v5" in run:
        return "v5_synthetic"
    if "v4" in run:
        return "v4_synthetic"
    return run or traj.condition or "unknown"


def strata_key_for_task(trajs: Sequence[MinedTrajectory]) -> str:
    """outcome-category × service when present; otherwise corpus tag."""
    if not trajs:
        return "unknown"
    sample = trajs[0]
    outcome = (sample.outcome_category or "").strip()
    services = [s for s in (sample.services or []) if s]
    if outcome:
        svc = services[0] if services else "filesystem-only"
        return f"{outcome}|{svc}"
    if sample.corpus:
        return f"corpus:{sample.corpus}"
    run = (sample.run or "").lower()
    if "v6" in run:
        return "corpus:v6"
    if "v5" in run:
        return "corpus:v5"
    if "v4" in run:
        return "corpus:v4"
    return "unknown"


def split_synthetic_tasks(
    synthetic: Sequence[MinedTrajectory],
    v3_eval: Sequence[MinedTrajectory],
    *,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    salt: str = SFT_SPLIT_SALT,
) -> SplitManifest:
    """Hold out ~20% of synthetic *tasks*; never mix instance_ids across the cut."""
    assert_train_is_synthetic(synthetic)
    instance_ids = sorted({t.instance_id for t in synthetic})
    train_ids: list[str] = []
    holdout_ids: list[str] = []
    for instance_id in instance_ids:
        if assign_split(instance_id, holdout_fraction, salt) == "eval":
            holdout_ids.append(instance_id)
        else:
            train_ids.append(instance_id)
    train_id_set = set(train_ids)
    holdout_id_set = set(holdout_ids)
    overlap = train_id_set & holdout_id_set
    if overlap:
        raise RuntimeError(f"instance_id leakage in split: {sorted(overlap)[:5]}")
    train_trajs = [t for t in synthetic if t.instance_id in train_id_set]
    holdout_trajs = [t for t in synthetic if t.instance_id in holdout_id_set]
    return SplitManifest(
        train_instance_ids=train_ids,
        holdout_instance_ids=holdout_ids,
        train_trajectories=train_trajs,
        holdout_trajectories=holdout_trajs,
        v3_eval_trajectories=list(v3_eval),
        salt=salt,
        holdout_fraction=holdout_fraction,
    )


def _task_split_map(trajs: Sequence[MinedTrajectory]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for traj in trajs:
        assigned = (traj.split or "train").strip() or "train"
        prev = mapping.get(traj.instance_id)
        if prev is not None and prev != assigned:
            raise ValueError(
                f"Inconsistent shipped split for {traj.instance_id}: "
                f"{prev!r} vs {assigned!r}"
            )
        mapping[traj.instance_id] = assigned
    return mapping


def split_by_shipped_fields(
    synthetic: Sequence[MinedTrajectory],
    v3_eval: Sequence[MinedTrajectory],
    *,
    salt: str = SHIPPED_SPLIT_SALT,
) -> ShippedSplitManifest:
    """Use the corpus task-level ``split`` field (train/dev/test). Never re-hash."""
    assert_train_is_synthetic(synthetic)
    task_split = _task_split_map(synthetic)
    train_ids = sorted(iid for iid, sp in task_split.items() if sp == "train")
    dev_ids = sorted(iid for iid, sp in task_split.items() if sp == "dev")
    test_ids = sorted(iid for iid, sp in task_split.items() if sp == "test")
    unused_ids = sorted(
        iid for iid, sp in task_split.items() if sp not in {"train", "dev", "test"}
    )
    train_set, dev_set, test_set = set(train_ids), set(dev_ids), set(test_ids)
    leaked = (train_set & dev_set) | (train_set & test_set) | (dev_set & test_set)
    if leaked:
        raise RuntimeError(
            f"instance_id leakage in shipped split: {sorted(leaked)[:5]}"
        )
    v3_ids = [t.instance_id for t in v3_eval]
    overlap = assert_train_eval_firewall(train_ids, v3_ids)
    return ShippedSplitManifest(
        train_instance_ids=train_ids,
        dev_instance_ids=dev_ids,
        test_instance_ids=test_ids,
        unused_instance_ids=unused_ids,
        train_trajectories=[t for t in synthetic if t.instance_id in train_set],
        dev_trajectories=[t for t in synthetic if t.instance_id in dev_set],
        test_trajectories=[t for t in synthetic if t.instance_id in test_set],
        v3_eval_trajectories=list(v3_eval),
        salt=salt,
        train_v3_overlap=overlap,
    )


def select_few_shot(
    train_examples: Sequence[SFTExample],
    *,
    n_high: int = 1,
    n_low: int = 1,
) -> list[dict[str, str]]:
    """Deterministic few-shot turns drawn only from the train bucket."""
    highs = sorted(
        (e for e in train_examples if e.label is SafetyLabel.HIGH_UNSAFE),
        key=lambda e: (e.instance_id, e.action_id),
    )
    lows = sorted(
        (e for e in train_examples if e.label is SafetyLabel.LOW_UNSAFE),
        key=lambda e: (e.instance_id, e.action_id),
    )
    chosen: list[SFTExample] = []
    chosen.extend(highs[:n_high])
    chosen.extend(lows[:n_low])
    messages: list[dict[str, str]] = []
    for example in chosen:
        messages.append({"role": "user", "content": example.user_text})
        messages.append({"role": "assistant", "content": example.assistant_text})
    return messages


def count_from_trajectories(trajs: Sequence[MinedTrajectory]) -> dict[str, int]:
    n_actions = sum(t.n_actions for t in trajs)
    n_high = sum(t.high_unsafe_count for t in trajs)
    return {
        "n_examples": n_actions,
        "n_high_unsafe": n_high,
        "n_low_unsafe": n_actions - n_high,
        "n_tasks": len({t.instance_id for t in trajs}),
        "n_trajectories": len(trajs),
    }


def count_labels(examples: Sequence[SFTExample]) -> dict[str, int]:
    high = sum(1 for e in examples if e.label is SafetyLabel.HIGH_UNSAFE)
    return {
        "n_examples": len(examples),
        "n_high_unsafe": high,
        "n_low_unsafe": len(examples) - high,
        "n_tasks": len({e.instance_id for e in examples}),
        "n_trajectories": len({e.trajectory_key for e in examples}),
    }


def load_synthetic_and_eval(
    train_paths: Iterable[str | Path],
    eval_path: str | Path,
) -> tuple[list[MinedTrajectory], list[MinedTrajectory]]:
    synthetic: list[MinedTrajectory] = []
    for path in train_paths:
        synthetic.extend(load_trajectories(path))
    assert_train_is_synthetic(synthetic)
    v3 = load_trajectories(eval_path)
    return synthetic, v3


def upsample_high(
    examples: Sequence[SFTExample],
    *,
    max_ratio: float = 1.0,
) -> list[SFTExample]:
    """Repeat high-unsafe rows until they meet ``max_ratio`` of the low count."""
    highs = [e for e in examples if e.label is SafetyLabel.HIGH_UNSAFE]
    lows = [e for e in examples if e.label is SafetyLabel.LOW_UNSAFE]
    if not highs or not lows:
        return list(examples)
    target = min(len(lows), int(len(lows) * max_ratio))
    if target <= len(highs):
        return list(examples)
    repeated: list[SFTExample] = []
    i = 0
    while len(repeated) < target:
        repeated.append(highs[i % len(highs)])
        i += 1
    return lows + repeated
