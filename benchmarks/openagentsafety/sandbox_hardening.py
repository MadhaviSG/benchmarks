"""Terminal-timeout cap and non-interactive shell setup for the OAS sandbox.

Agents sometimes pass ``timeout`` in milliseconds (``120000``), which the
OpenHands terminal tool reads as seconds (33 hours). A pager (``git log``) or a
dead bash session then blocks the rollout until the instance timeout.

The agent loop and the terminal tool run inside the agent-server container, so
the cap is applied there. The harness uploads this file into the container's
site-packages and registers a marker tool from this module on the client. The
agent server imports every module named in ``tool_module_qualnames`` when it
creates a conversation, and importing this module there patches
``TerminalTool.action_from_arguments`` so the action carries the capped value.

This file must stay self-contained: standard library only at module level, no
imports from ``benchmarks``. OpenHands is imported lazily inside functions.
"""

from __future__ import annotations

import math
import sys
from typing import Any


MS_THRESHOLD_SECONDS = 3600.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0

# Registered on the client only so its module qualname reaches the server.
MARKER_TOOL_NAME = "oas_terminal_timeout_cap"
MODULE_QUALNAME = "benchmarks.openagentsafety.sandbox_hardening"

NONINTERACTIVE_ENV: tuple[tuple[str, str], ...] = (
    ("PAGER", "cat"),
    ("GIT_PAGER", "cat"),
    ("GIT_TERMINAL_PROMPT", "0"),
)


def cap_terminal_timeout(raw: Any, default: float | None = None) -> float | None:
    """Return a terminal timeout in seconds, clamped to [1, 600].

    Values above 3600 are read as milliseconds. ``None`` or an unparseable
    value returns ``default``.
    """
    if raw is None or isinstance(raw, bool):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if math.isnan(value):
        return default
    if value > MS_THRESHOLD_SECONDS:
        value = value / 1000.0
    return min(max(value, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def noninteractive_env_command() -> str:
    """Shell command that makes PAGER/GIT_PAGER/GIT_TERMINAL_PROMPT persistent.

    The agent's terminal is an interactive non-login bash started after
    workspace setup, so it reads /etc/bash.bashrc and ~/.bashrc. Lines are
    appended only if missing. /etc/environment covers PAM logins and
    ``git config --system core.pager cat`` covers git run without that env.
    """
    exports = "export " + " ".join(f"{k}={v}" for k, v in NONINTERACTIVE_ENV)
    pairs = " ".join(f"{k}={v}" for k, v in NONINTERACTIVE_ENV)
    return (
        f"for f in /etc/bash.bashrc ~/.bashrc; do "
        f"grep -qxF '{exports}' \"$f\" 2>/dev/null || "
        f"printf '%s\\n' '{exports}' >> \"$f\"; done; "
        f"for kv in {pairs}; do "
        f'grep -qxF "$kv" /etc/environment 2>/dev/null || '
        f'echo "$kv" >> /etc/environment; done; '
        f"git config --system core.pager cat 2>/dev/null || true"
    )


def install_terminal_timeout_cap() -> bool:
    """Patch TerminalTool so parsed tool-call timeouts are capped. Idempotent."""
    try:
        from openhands.tools.terminal.definition import TerminalTool
    except ImportError:
        return False
    current = TerminalTool.action_from_arguments
    if getattr(current, "_oas_timeout_cap", False):
        return True

    def action_from_arguments(self: Any, arguments: dict[str, Any]) -> Any:
        if isinstance(arguments, dict) and arguments.get("timeout") is not None:
            capped = cap_terminal_timeout(arguments["timeout"])
            if capped is not None:
                arguments = {**arguments, "timeout": capped}
        return current(self, arguments)

    action_from_arguments._oas_timeout_cap = True  # type: ignore[attr-defined]
    TerminalTool.action_from_arguments = action_from_arguments  # type: ignore[method-assign]
    return True


def _marker_tool_factory(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def register_for_agent_server() -> None:
    """Client side: make RemoteConversation send this module to the server."""
    from openhands.sdk.tool.registry import list_registered_tools, register_tool

    if MARKER_TOOL_NAME not in list_registered_tools():
        register_tool(MARKER_TOOL_NAME, _marker_tool_factory)
    install_terminal_timeout_cap()


# Inside the agent-server process, importing this module applies the cap and
# registers the marker, so GET /api/tools/ shows the import happened.
if "openhands.agent_server" in sys.modules:
    register_for_agent_server()
