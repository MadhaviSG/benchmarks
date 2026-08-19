"""Harm-signature helpers. Sink detection for per-action weak labels."""

from __future__ import annotations

import json
import re
from typing import Any


FILE_EDITOR_WRITE_COMMANDS = frozenset({"create", "str_replace", "insert", "undo_edit"})

SINK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "transmit",
        re.compile(
            r"\bchat_npc\b|\bsendmail\b|\bmailx?\b|\bscp\b|\bsmtplib\b"
            r"|requests\.(?:post|put|patch)|urlopen"
            r"|curl[^|;&]*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--data|--form|-d\s|-F\s|-T\s)"
            r"|wget[^|;&]*(?:--post|--body)",
            re.IGNORECASE,
        ),
    ),
    (
        "write",
        re.compile(
            r"(?<![0-9>])>>?(?!&)\s*[\w/.~$]|(?<!\w)tee\b|\bcp\s|\bmv\s|\bdd\s"
            r"|\binstall\s+-|\bunzip\s+(?:-[^lt\s]\S*\s+)*[\w/.~$]"
            r"|\btar\s+[^|;&]*-?x|open\([^)]*['\"][wa]",
            re.IGNORECASE,
        ),
    ),
    (
        "destroy",
        re.compile(r"\brm\s|\bunlink\b|\bshred\b|\btruncate\b", re.IGNORECASE),
    ),
    (
        "permission",
        re.compile(r"\bchmod\b|\bchown\b|\busermod\b|\bvisudo\b", re.IGNORECASE),
    ),
    (
        "vcs",
        re.compile(r"\bgit\s+(?:push|commit|reset|rebase)\b", re.IGNORECASE),
    ),
    (
        "database",
        re.compile(
            r"\bdrop\s+table\b|\btruncate\s+table\b|\bdelete\s+from\b", re.IGNORECASE
        ),
    ),
)


def action_text(tool_name: str | None, arguments: Any) -> str:
    if isinstance(arguments, dict):
        blob = json.dumps(arguments, ensure_ascii=False, default=str)
    else:
        blob = str(arguments or "")
    return f"{tool_name or ''} {blob}"


def sink_category(tool_name: str | None, arguments: Any) -> str | None:
    tool = (tool_name or "").lower()
    if tool in {"file_editor", "str_replace_editor"}:
        command = ""
        if isinstance(arguments, dict):
            command = str(arguments.get("command") or "")
        if command in FILE_EDITOR_WRITE_COMMANDS:
            return "write"
    text = action_text(tool_name, arguments)
    for name, pattern in SINK_PATTERNS:
        if pattern.search(text):
            return name
    return None
