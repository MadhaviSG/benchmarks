"""Abstract critic and a constant critic used in tests."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from safety_monitor.types import (
    ActionVerdict,
    ObservableAction,
    SafetyLabel,
    Step,
    Trajectory,
    TrajectoryLabels,
)


class SafetyCritic(ABC):
    """Labels observable actions without seeing actor weights or chain-of-thought."""

    critic_id: str = "base"

    @abstractmethod
    def label_action(
        self,
        action: ObservableAction,
        history: Sequence[Step],
        *,
        instruction: str | None = None,
        env_state: dict[str, Any] | None = None,
    ) -> ActionVerdict:
        raise NotImplementedError

    def label_trajectory(self, trajectory: Trajectory) -> TrajectoryLabels:
        verdicts: list[ActionVerdict] = []
        history: list[Step] = []
        for step in trajectory.steps:
            if step.action is not None:
                verdicts.append(
                    self.label_action(
                        step.action,
                        history,
                        instruction=trajectory.instruction,
                        env_state=trajectory.env_state,
                    )
                )
            history.append(step)
        return TrajectoryLabels(
            instance_id=trajectory.instance_id,
            critic_id=self.critic_id,
            verdicts=verdicts,
        )


class ConstantCritic(SafetyCritic):
    """Always returns the same label. Used in tests."""

    def __init__(self, label: SafetyLabel, critic_id: str = "constant") -> None:
        self._label = label
        self.critic_id = critic_id

    def label_action(
        self,
        action: ObservableAction,
        history: Sequence[Step],
        *,
        instruction: str | None = None,
        env_state: dict[str, Any] | None = None,
    ) -> ActionVerdict:
        del history, instruction, env_state
        return ActionVerdict(
            action_id=action.action_id,
            label=self._label,
            critic_id=self.critic_id,
            tool_name=action.tool_name,
        )
