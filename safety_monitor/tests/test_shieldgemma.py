"""ShieldGemma critic: prompt template and Yes/No → SafetyLabel mapping.

HF/GPU are mocked; these tests never load weights.
"""

from __future__ import annotations

import pytest
from safety_monitor.critic.shieldgemma import (
    ShieldGemmaCritic,
    format_shieldgemma_prompt,
    map_shieldgemma_verdict,
    resolve_shieldgemma_model_path,
)
from safety_monitor.types import ObservableAction, SafetyLabel, Step


def _action(command: str = "rm -rf /secrets") -> ObservableAction:
    return ObservableAction(
        action_id="a1",
        tool_name="terminal",
        arguments={"command": command},
    )


@pytest.mark.parametrize(
    "raw,yes_prob,expected",
    [
        ("Yes", None, SafetyLabel.HIGH_UNSAFE),
        ("yes, the prompt violates the policy", None, SafetyLabel.HIGH_UNSAFE),
        ("Violate", None, SafetyLabel.HIGH_UNSAFE),
        ("violation of No Harmful Agent Actions", None, SafetyLabel.HIGH_UNSAFE),
        ("high", None, SafetyLabel.HIGH_UNSAFE),
        ("No", None, SafetyLabel.LOW_UNSAFE),
        ("no, this is a listing command", None, SafetyLabel.LOW_UNSAFE),
        ("", None, SafetyLabel.LOW_UNSAFE),
        (None, 0.91, SafetyLabel.HIGH_UNSAFE),
        (None, 0.10, SafetyLabel.LOW_UNSAFE),
        ("No", 0.8, SafetyLabel.HIGH_UNSAFE),  # probability wins
        ("Yes", 0.1, SafetyLabel.LOW_UNSAFE),
    ],
)
def test_map_shieldgemma_verdict(raw, yes_prob, expected):
    assert map_shieldgemma_verdict(raw, yes_prob) is expected


def test_prompt_uses_shieldgemma_template_and_action():
    history = [
        Step(
            action=ObservableAction(
                action_id="h",
                tool_name="terminal",
                arguments={"command": "ls /workspace"},
            )
        )
    ]
    prompt = format_shieldgemma_prompt(_action(), history)
    assert "<start_of_turn>" in prompt
    assert "Human Question:" in prompt
    assert "Does the human question violate" in prompt
    assert "rm -rf /secrets" in prompt
    assert "ls /workspace" in prompt
    assert "Yes" in prompt and "No" in prompt


def test_critic_mock_completer_yes_is_high():
    critic = ShieldGemmaCritic(complete=lambda _messages: "Yes")
    verdict = critic.label_action(_action(), [])
    assert verdict.label is SafetyLabel.HIGH_UNSAFE
    assert verdict.critic_id == "shieldgemma"
    assert verdict.raw_response == "Yes"


def test_critic_mock_completer_no_is_low():
    critic = ShieldGemmaCritic(complete=lambda _messages: "No")
    verdict = critic.label_action(_action("ls"), [])
    assert verdict.label is SafetyLabel.LOW_UNSAFE


def test_critic_mock_score_fn():
    critic = ShieldGemmaCritic(score_fn=lambda _prompt: (0.99, "yes_prob=0.9900"))
    verdict = critic.label_action(_action(), [])
    assert verdict.label is SafetyLabel.HIGH_UNSAFE
    assert "0.99" in (verdict.raw_response or "")


def test_missing_local_path_errors_without_download(monkeypatch):
    monkeypatch.delenv("SHIELDGEMMA_MODEL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="does not download"):
        resolve_shieldgemma_model_path(None)
    with pytest.raises(RuntimeError, match="not found"):
        resolve_shieldgemma_model_path("/definitely/not/a/real/shieldgemma")
