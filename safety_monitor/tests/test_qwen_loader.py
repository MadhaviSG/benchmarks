"""Qwen critic loader: path resolution and PromptedSafetyCritic wiring. No HF."""

from __future__ import annotations

import pytest
from safety_monitor.critic.prompted import PromptedSafetyCritic
from safety_monitor.critic.qwen import (
    load_qwen_critic,
    resolve_qwen_adapter_dir,
    resolve_qwen_model_path,
)
from safety_monitor.types import ObservableAction, SafetyLabel


def test_missing_qwen_path_errors_without_download(monkeypatch):
    monkeypatch.delenv("QWEN_MODEL_PATH", raising=False)
    monkeypatch.delenv("SAFETY_MONITOR_QWEN", raising=False)
    monkeypatch.setattr(
        "safety_monitor.critic.qwen.find_qwen_on_disk",
        lambda _explicit=None: (None, "none"),
    )
    with pytest.raises(RuntimeError, match="does not download"):
        resolve_qwen_model_path(None)


def test_explicit_missing_dir_rejected(tmp_path, monkeypatch):
    missing = tmp_path / "no-such-qwen"
    monkeypatch.setattr(
        "safety_monitor.critic.qwen.find_qwen_on_disk",
        lambda explicit=None: (str(missing), "explicit"),
    )
    with pytest.raises(RuntimeError, match="does not download"):
        resolve_qwen_model_path(str(missing))


def test_adapter_dir_optional_and_default(tmp_path, monkeypatch):
    monkeypatch.delenv("QWEN_ADAPTER_DIR", raising=False)
    monkeypatch.setattr(
        "safety_monitor.critic.qwen._DEFAULT_ADAPTER", tmp_path / "missing-adapter"
    )
    assert resolve_qwen_adapter_dir(None) is None
    assert resolve_qwen_adapter_dir("") is None
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    assert resolve_qwen_adapter_dir(str(adapter)) == str(adapter)


def test_load_qwen_critic_uses_hf_completer_mock(tmp_path, monkeypatch):
    model_dir = tmp_path / "qwen"
    model_dir.mkdir()
    monkeypatch.setattr(
        "safety_monitor.critic.qwen.find_qwen_on_disk",
        lambda explicit=None: (str(model_dir), "test"),
    )
    monkeypatch.setattr(
        "safety_monitor.critic.qwen.resolve_qwen_adapter_dir",
        lambda explicit=None: None,
    )

    def fake_completer(model_path, adapter_dir=None, **_kwargs):
        assert model_path == str(model_dir)
        assert adapter_dir is None

        def complete(messages):
            return "Label: high-unsafe"

        return complete

    monkeypatch.setattr("safety_monitor.critic.qwen.make_hf_completer", fake_completer)
    critic = load_qwen_critic(model_path=str(model_dir))
    assert isinstance(critic, PromptedSafetyCritic)
    assert critic.critic_id == "qwen-sft"
    verdict = critic.label_action(
        ObservableAction(
            action_id="1",
            tool_name="terminal",
            arguments={"command": "rm -rf /"},
        ),
        [],
    )
    assert verdict.label is SafetyLabel.HIGH_UNSAFE
