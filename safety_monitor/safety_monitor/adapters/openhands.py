"""Wrap a SafetyCritic as an OpenHands-compatible analyzer (label only).

The OpenHands SDK is imported lazily inside ``as_openhands_analyzer`` so this
module type-checks without the SDK on the path.
"""

from __future__ import annotations

from typing import Any

from safety_monitor.types import ObservableAction, SafetyLabel, Step


def critic_label_to_risk_name(label: SafetyLabel) -> str:
    return "HIGH" if label is SafetyLabel.HIGH_UNSAFE else "LOW"


def _dump_arguments(action: Any) -> dict[str, Any]:
    if action is None:
        return {}
    if isinstance(action, dict):
        return dict(action)
    dump = getattr(action, "model_dump", None)
    if callable(dump):
        data = dump()
        return data if isinstance(data, dict) else {}
    return {}


def _action_event_to_observable(event: Any) -> ObservableAction:
    action_id = str(getattr(event, "id", "") or "action")
    tool_name = str(getattr(event, "tool_name", None) or "unknown")
    return ObservableAction(
        action_id=action_id,
        tool_name=tool_name,
        arguments=_dump_arguments(getattr(event, "action", None)),
    )


def _events_to_history(events: list[Any]) -> list[Step]:
    steps: list[Step] = []
    for event in events:
        source = str(getattr(event, "source", "") or "").upper()
        if source != "AGENT":
            continue
        if not getattr(event, "tool_name", None):
            continue
        steps.append(Step(action=_action_event_to_observable(event)))
    return steps


def sync_analyzer_history(analyzer: Any, events: list[Any]) -> None:
    """Feed conversation events into analyzers that implement set_events."""
    if analyzer is None:
        return
    hook = getattr(analyzer, "set_events", None)
    if callable(hook):
        hook(events)


class IndependentMonitorAnalyzer:
    """Labels actions via the independent critic; does not block by itself."""

    def __init__(self, critic: Any, *, history_limit: int = 20) -> None:
        self._critic = critic
        self.history_limit = history_limit
        self._events: list[Any] = []

    def set_events(self, events: list[Any]) -> None:
        self._events = list(events)

    def security_risk(self, action: Any) -> Any:
        history = _events_to_history(self._events[-self.history_limit :])
        observable = _action_event_to_observable(action)
        verdict = self._critic.label_action(observable, history)
        try:
            from openhands.sdk.security.risk import (  # pyright: ignore[reportMissingImports]
                SecurityRisk,
            )
        except ImportError:
            return critic_label_to_risk_name(verdict.label)
        if verdict.label is SafetyLabel.HIGH_UNSAFE:
            return SecurityRisk.HIGH
        return SecurityRisk.LOW


def as_openhands_analyzer(critic: Any, *, history_limit: int = 20) -> Any:
    analyzer = IndependentMonitorAnalyzer(critic, history_limit=history_limit)
    try:
        from openhands.sdk.security.analyzer import (  # pyright: ignore[reportMissingImports]
            SecurityAnalyzerBase,
        )
    except ImportError:
        return analyzer

    class _BoundAnalyzer(SecurityAnalyzerBase, IndependentMonitorAnalyzer):
        pass

    bound = _BoundAnalyzer.__new__(_BoundAnalyzer)
    IndependentMonitorAnalyzer.__init__(bound, critic, history_limit=history_limit)
    return bound
