"""OpenHands adapter history conversion and set_events hook. No GPU."""

from __future__ import annotations

import pytest
from safety_monitor.adapters.openhands import (
    _action_event_to_observable,
    _events_to_history,
    critic_label_to_risk_name,
    sync_analyzer_history,
)
from safety_monitor.critic.base import ConstantCritic
from safety_monitor.types import SafetyLabel


class _FakeEvent:
    def __init__(self, source, tool_name=None, action=None, id="e"):
        self.source = source
        self.tool_name = tool_name
        self.action = action
        self.id = id


def test_events_to_history_keeps_agent_tool_calls():
    events = [
        _FakeEvent("USER"),
        _FakeEvent("AGENT", "terminal", {"command": "ls"}),
        _FakeEvent("AGENT", "terminal", {"command": "rm -rf /tmp/x"}),
    ]
    steps = _events_to_history(events)
    assert len(steps) == 2
    assert steps[0].action is not None
    assert steps[1].action is not None
    assert steps[0].action.tool_name == "terminal"
    assert steps[1].action.arguments["command"].startswith("rm -rf")


def test_action_event_to_observable_uses_model_dump():
    class Payload:
        def model_dump(self):
            return {"path": "/etc/passwd"}

    event = _FakeEvent("AGENT", "file_editor", Payload(), id="edit-1")
    obs = _action_event_to_observable(event)
    assert obs.action_id == "edit-1"
    assert obs.arguments["path"] == "/etc/passwd"


def test_sync_analyzer_history_calls_set_events():
    class Analyzer:
        def __init__(self):
            self.seen = None

        def set_events(self, events):
            self.seen = list(events)

    analyzer = Analyzer()
    events = [_FakeEvent("AGENT", "terminal", {"command": "cat a"})]
    sync_analyzer_history(analyzer, events)
    assert analyzer.seen == events


def test_sync_analyzer_history_ignores_analyzers_without_hook():
    sync_analyzer_history(object(), [])  # must not raise
    sync_analyzer_history(None, [])


def test_critic_label_to_risk_name():
    assert critic_label_to_risk_name(SafetyLabel.HIGH_UNSAFE) == "HIGH"
    assert critic_label_to_risk_name(SafetyLabel.LOW_UNSAFE) == "LOW"


def test_as_openhands_analyzer_uses_history_after_set_events():
    pytest.importorskip("openhands.sdk")
    from safety_monitor.adapters.openhands import as_openhands_analyzer

    seen_history_len = []

    class RecordingCritic(ConstantCritic):
        def label_action(self, action, history, **kwargs):
            seen_history_len.append(len(history))
            return super().label_action(action, history, **kwargs)

    critic = RecordingCritic(SafetyLabel.HIGH_UNSAFE, critic_id="rec")
    analyzer = as_openhands_analyzer(critic)
    prior = _FakeEvent("AGENT", "terminal", {"command": "ls /workspace"})
    current = _FakeEvent("AGENT", "terminal", {"command": "rm -rf /secrets"})
    analyzer.set_events([prior])

    from openhands.sdk.security.risk import (  # pyright: ignore[reportMissingImports]
        SecurityRisk,
    )

    risk = analyzer.security_risk(current)
    assert risk is SecurityRisk.HIGH
    assert seen_history_len == [1]
