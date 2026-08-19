"""Load the local Qwen SFT critic. Never downloads weights."""

from __future__ import annotations

import os
from pathlib import Path

from safety_monitor.critic.prompted import PromptedSafetyCritic
from safety_monitor.sft.backends import find_qwen_on_disk, make_hf_completer


# repo/safety_monitor/safety_monitor/critic/qwen.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ADAPTER = _REPO_ROOT / "analysis_outputs" / "qwen_sft" / "hf" / "adapter"

_MISSING_QWEN = (
    "Qwen critic needs local weights. Set QWEN_MODEL_PATH or SAFETY_MONITOR_QWEN "
    "(or pass --qwen-model-path). This environment does not download models."
)


def resolve_qwen_model_path(explicit: str | None = None) -> str:
    """Return a local directory that contains Qwen weights.

    Raises RuntimeError if nothing is on disk. Hub ids are rejected so we never
    trigger a HuggingFace download.
    """
    requested = (
        explicit
        or os.environ.get("QWEN_MODEL_PATH")
        or os.environ.get("SAFETY_MONITOR_QWEN")
    )
    path, _note = find_qwen_on_disk(requested)
    if path and Path(path).expanduser().exists():
        return str(Path(path).expanduser())
    raise RuntimeError(_MISSING_QWEN)


def resolve_qwen_adapter_dir(explicit: str | None = None) -> str | None:
    """Optional LoRA adapter. Empty / missing paths are treated as no adapter."""
    if explicit is not None:
        text = explicit.strip()
        if not text:
            return None
        path = Path(text).expanduser()
        if not path.exists():
            raise RuntimeError(
                f"QWEN_ADAPTER_DIR / --qwen-adapter-dir does not exist: {path}"
            )
        return str(path)

    env = os.environ.get("QWEN_ADAPTER_DIR")
    if env is not None:
        text = env.strip()
        if not text:
            return None
        path = Path(text).expanduser()
        if not path.exists():
            raise RuntimeError(f"QWEN_ADAPTER_DIR does not exist: {path}")
        return str(path)

    if _DEFAULT_ADAPTER.is_dir():
        return str(_DEFAULT_ADAPTER)
    return None


def load_qwen_critic(
    *,
    model_path: str | None = None,
    adapter_dir: str | None = None,
    critic_id: str = "qwen-sft",
) -> PromptedSafetyCritic:
    """Build a PromptedSafetyCritic backed by local HF Qwen (+ optional LoRA)."""
    resolved_model = resolve_qwen_model_path(model_path)
    resolved_adapter = resolve_qwen_adapter_dir(adapter_dir)
    complete = make_hf_completer(resolved_model, adapter_dir=resolved_adapter)
    return PromptedSafetyCritic(complete, critic_id=critic_id)
