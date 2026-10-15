"""JSON extraction and a retrying JSON call for the v8 pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any


Completer = Callable[[list[dict[str, str]]], str]

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Parse one JSON object from a model reply (fences and surrounding prose allowed)."""
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE_RE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    last: Exception | None = None
    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError as exc:
            last = exc
            continue
        if isinstance(obj, dict):
            return obj
        last = ValueError(f"top-level JSON is {type(obj).__name__}, not an object")
    raise ValueError(f"no JSON object found: {last}")


def call_json(
    complete: Completer,
    messages: list[dict[str, str]],
    required_keys: Sequence[str],
    retries: int = 2,
) -> dict[str, Any]:
    """Call ``complete`` until it returns a JSON object with ``required_keys``.

    On failure the reply and a correction request are appended and the call is
    retried, up to ``retries`` extra times.
    """
    convo = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        reply = complete(convo)
        try:
            obj = extract_json(reply)
            missing = [k for k in required_keys if k not in obj]
            if missing:
                raise ValueError(f"missing keys {missing}")
            return obj
        except ValueError as exc:
            last_error = str(exc)
        convo = convo + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "Your reply was not a single valid JSON object with the required fields: "
                f"{last_error}. Return only the JSON object.",
            },
        ]
    raise ValueError(f"no valid JSON after {retries + 1} attempts: {last_error}")
