"""Lexical decontamination: flag shared pivotal/instruction n-grams across tasks."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from safety_monitor.synthesis.v5_generate import RenderedPair


DEFAULT_MIN_N = 6
DEFAULT_MAX_TASKS = 3


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def ngrams(text: str, n: int) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{2,}", _normalize(text))
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def pivotal_text(rp: RenderedPair) -> str:
    for act in rp.unsafe_acts:
        if act.pivotal:
            return act.command + " " + act.summary + " " + act.thought
    return ""


def decontamination_report(
    rendered: Iterable[RenderedPair],
    *,
    min_n: int = DEFAULT_MIN_N,
    max_tasks: int = DEFAULT_MAX_TASKS,
) -> dict:
    """Find n-grams shared between instruction and pivotal action across >max_tasks pairs."""
    ng_counts: Counter[str] = Counter()
    ng_tasks: dict[str, set[str]] = defaultdict(set)
    flagged: list[dict] = []

    for rp in rendered:
        instr = rp.problem_statement
        pivot = pivotal_text(rp)
        shared = ngrams(instr, min_n) & ngrams(pivot, min_n)
        for gram in shared:
            ng_counts[gram] += 1
            ng_tasks[gram].add(rp.seed.instance_id)

    for gram, tasks in ng_tasks.items():
        if len(tasks) > max_tasks:
            flagged.append(
                {"ngram": gram, "n_tasks": len(tasks), "instance_ids": sorted(tasks)}
            )

    return {
        "min_n": min_n,
        "max_tasks_allowed": max_tasks,
        "n_flagged_ngrams": len(flagged),
        "flagged": sorted(flagged, key=lambda x: -x["n_tasks"])[:50],
        "ok": len(flagged) == 0,
    }


def write_report(path: str | Path, report: dict) -> None:
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
