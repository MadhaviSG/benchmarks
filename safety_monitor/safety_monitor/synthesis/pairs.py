"""Contrastive pair models used by v4/v5 synthesis and SFT data loading."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from safety_monitor.types import SafetyLabel


CLEAN = "clean"
HARMFUL = "harmful"
PREVENTED = "prevented"


def assign_split(
    instance_id: str,
    eval_fraction: float = 0.2,
    salt: str = "critic-pairs",
) -> str:
    """Deterministic task-level train/eval split. Returns 'train' or 'eval'."""
    digest = hashlib.sha1(f"{salt}:{instance_id}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "eval" if bucket < eval_fraction else "train"


class LabeledStep(BaseModel):
    action_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    observation: str | None = None
    is_error: bool = False
    sink: str | None = None
    contrast_lift: float = 0.0
    label: SafetyLabel = SafetyLabel.LOW_UNSAFE
    label_source: str = "unknown"


class MinedTrajectory(BaseModel):
    key: str
    instance_id: str
    run: str
    condition: str
    actor: str | None = None
    role: str
    rule_based: int | None = None
    security_blocked: bool = False
    instruction: str | None = None
    signature_available: bool = False
    steps: list[LabeledStep] = Field(default_factory=list)
    split: str = "train"

    @property
    def n_actions(self) -> int:
        return len(self.steps)

    @property
    def high_unsafe_count(self) -> int:
        return sum(1 for step in self.steps if step.label is SafetyLabel.HIGH_UNSAFE)


class ContrastPair(BaseModel):
    pair_id: str
    instance_id: str
    condition: str
    harmful_key: str
    safe_key: str
    harmful_actor: str | None = None
    safe_actor: str | None = None
    n_actions_harmful: int = 0
    n_actions_safe: int = 0
    harmful_high_unsafe: int = 0
