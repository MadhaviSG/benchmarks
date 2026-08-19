"""Build OpenHands security analyzers from safety_monitor critics.

Used by OAS `create_security_analyzer("qwen"|"shieldgemma")`. OpenHands is
imported only when an analyzer is actually constructed.
"""

from __future__ import annotations

from typing import Any


_QWEN = "qwen"
_SHIELDGEMMA = frozenset({"shieldgemma", "shield-gemma"})


def normalize_analyzer_name(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def create_external_analyzer(
    name: str,
    *,
    qwen_model_path: str | None = None,
    qwen_adapter_dir: str | None = None,
    shieldgemma_model_path: str | None = None,
) -> Any:
    """Return an OpenHands SecurityAnalyzerBase for qwen or shieldgemma.

    Raises ValueError for unknown names. HuggingFace is not imported until the
    matching critic loader runs.
    """
    key = normalize_analyzer_name(name)
    from safety_monitor.adapters.openhands import as_openhands_analyzer

    if key == _QWEN:
        from safety_monitor.critic.qwen import load_qwen_critic

        critic = load_qwen_critic(
            model_path=qwen_model_path,
            adapter_dir=qwen_adapter_dir,
        )
        return as_openhands_analyzer(critic)

    if key in _SHIELDGEMMA:
        from safety_monitor.critic.shieldgemma import load_shieldgemma_critic

        critic = load_shieldgemma_critic(model_path=shieldgemma_model_path)
        return as_openhands_analyzer(critic)

    raise ValueError(f"Unknown security analyzer: {name}")
