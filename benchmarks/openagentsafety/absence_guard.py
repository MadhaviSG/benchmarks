"""Refuse a rollout whose absence grader would fire before the agent acts."""

from __future__ import annotations

import re
from typing import Any


_ABSENCE = re.compile(
    r"if\s+os\.path\.isfile\(\s*TARGET\s*\)\s*:\s*\n"
    r"(?:.*\n){0,3}?"
    r"\s*return False",
    re.M,
)
_TARGET = re.compile(r"^TARGET\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def unstaged_absence_target(instance_data: dict[str, Any]) -> str | None:
    """The ``/workspace`` path an absence grader scores, if no staged file covers it."""
    code = str(instance_data.get("evaluator_code") or "")
    if not _ABSENCE.search(code):
        return None
    match = _TARGET.search(code)
    if match is None:
        return None
    target = match.group(1)
    if not target.startswith("/workspace/"):
        return None
    relative = target.removeprefix("/workspace/").lstrip("/")
    if not relative:
        return None
    for spec in instance_data.get("workspace_files") or []:
        text = str(spec).replace("\\", "/").rstrip("/")
        if text == relative or text.endswith("/" + relative):
            return None
    return target
