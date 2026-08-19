"""LLM-prompted critic over observable actions only."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from safety_monitor.critic.base import SafetyCritic
from safety_monitor.critic.prompts import SYSTEM_PROMPT, render_history
from safety_monitor.types import (
    ActionVerdict,
    ObservableAction,
    SafetyLabel,
    Step,
)


CompletionFn = Callable[[list[dict[str, str]]], str]

_LABEL_RE = re.compile(
    r"\b(high-unsafe|low-unsafe|high_unsafe|low_unsafe)\b",
    re.IGNORECASE,
)


def format_action(action: ObservableAction) -> str:
    args = json.dumps(action.arguments, ensure_ascii=False, default=str)
    summary = f" summary={action.summary}" if action.summary else ""
    return f"{action.tool_name} {args}{summary} id={action.action_id}"


def format_history(history: Sequence[Step], *, max_steps: int = 20) -> str:
    recent = list(history)[-max_steps:]
    lines: list[str] = []
    for i, step in enumerate(recent, start=1):
        if step.action is not None:
            lines.append(f"{i}. ACTION {format_action(step.action)}")
        if step.observation is not None and step.observation.text:
            text = step.observation.text
            if len(text) > 400:
                text = text[:200] + " … " + text[-200:]
            err = " ERROR" if step.observation.is_error else ""
            lines.append(f"   OBS{err}: {text}")
    return "\n".join(lines)


def parse_label(raw: str) -> SafetyLabel:
    match = _LABEL_RE.search(raw or "")
    if match and match.group(1).lower().replace("_", "-") == "high-unsafe":
        return SafetyLabel.HIGH_UNSAFE
    return SafetyLabel.LOW_UNSAFE


class PromptedSafetyCritic(SafetyCritic):
    def __init__(
        self,
        complete: CompletionFn,
        critic_id: str = "prompted",
        few_shot: Sequence[dict[str, str]] | None = None,
    ) -> None:
        self.complete = complete
        self.critic_id = critic_id
        self.few_shot = list(few_shot) if few_shot else []

    def label_action(
        self,
        action: ObservableAction,
        history: Sequence[Step],
        *,
        instruction: str | None = None,
        env_state: dict[str, Any] | None = None,
    ) -> ActionVerdict:
        del env_state
        user = render_history(
            format_history(history),
            format_action(action),
            instruction,
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.few_shot)
        messages.append({"role": "user", "content": user})
        raw = self.complete(messages)
        return ActionVerdict(
            action_id=action.action_id,
            label=parse_label(raw),
            raw_response=raw,
            critic_id=self.critic_id,
            tool_name=action.tool_name,
        )
