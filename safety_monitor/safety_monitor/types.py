"""Framework-agnostic types for the external safety critic."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SafetyLabel(str, Enum):
    """Per-action safety label released by the critic."""

    HIGH_UNSAFE = "high-unsafe"
    LOW_UNSAFE = "low-unsafe"


class ObservableAction(BaseModel):
    """A single tool action visible to an external monitor."""

    action_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None


class Observation(BaseModel):
    """Environment response after an action."""

    text: str = ""
    is_error: bool = False


class Step(BaseModel):
    """One action/observation pair in a trajectory."""

    action: ObservableAction | None = None
    observation: Observation | None = None


class Trajectory(BaseModel):
    """An agent's observable run, independent of the host framework."""

    instance_id: str
    steps: list[Step] = Field(default_factory=list)
    instruction: str | None = None
    env_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionVerdict(BaseModel):
    """Critic label for one action."""

    action_id: str
    label: SafetyLabel
    raw_response: str | None = None
    critic_id: str = "prompted"
    tool_name: str | None = None


class TrajectoryLabels(BaseModel):
    """All per-action labels for one trajectory."""

    instance_id: str
    critic_id: str
    actor: str | None = None
    verdicts: list[ActionVerdict] = Field(default_factory=list)

    @property
    def n_actions(self) -> int:
        return len(self.verdicts)

    @property
    def high_unsafe_count(self) -> int:
        return sum(1 for v in self.verdicts if v.label is SafetyLabel.HIGH_UNSAFE)


class TrajectoryGroundTruth(BaseModel):
    """Three complementary trajectory-level labels."""

    instance_id: str
    rule_based: int | None = Field(default=None)
    llm_judge: int | None = Field(default=None)
    human: int | None = None
