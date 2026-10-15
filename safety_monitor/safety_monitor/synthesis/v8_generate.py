"""Generate and review v8 candidates, one JSON line per brief (resumable)."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v8_briefs import Brief
from safety_monitor.synthesis.v8_llm import Completer, call_json
from safety_monitor.synthesis.v8_prompts import Vars, render_generate, render_review


GEN_KEYS = (
    "task_id",
    "task_md",
    "checkpoints_md",
    "scenarios_json",
    "dependencies_yml",
    "evaluator_py",
    "initial_resources",
    "governing_policy_location",
    "safety_boundary",
    "expected_safe_behavior",
    "unsafe_outcome_state",
    "tools_needed",
    "coverage",
)
REVIEW_KEYS = ("runnable", "checks", "blocking_issues")


def generate_one(
    brief: Brief,
    gen: Completer,
    rev: Completer | None,
    vars: Vars,
    policies: dict[str, str],
) -> dict[str, Any]:
    policy = policies[brief.outcome_category]
    out: dict[str, Any] = {
        "brief": brief.model_dump(),
        "candidate": None,
        "review": None,
        "gen_error": None,
    }
    try:
        out["candidate"] = call_json(
            gen, render_generate(brief, vars, policy), GEN_KEYS
        )
    except (
        Exception
    ) as exc:  # recorded, not raised: one bad brief must not stop the batch
        out["gen_error"] = f"{type(exc).__name__}: {exc}"
        return out
    if rev is not None:
        cand_text = json.dumps(out["candidate"], indent=2, ensure_ascii=False)
        try:
            out["review"] = call_json(
                rev, render_review(cand_text, vars, policy), REVIEW_KEYS
            )
        except Exception as exc:
            out["review"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def done_brief_ids(out_path: Path) -> set[str]:
    """Brief ids already in ``out_path`` without a generation error."""
    done: set[str] = set()
    path = Path(out_path)
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("gen_error"):
            done.add(row["brief"]["brief_id"])
    return done


def generate_batch(
    briefs: list[Brief],
    out_path: Path,
    gen: Completer,
    rev: Completer | None,
    vars: Vars,
    policies: dict[str, str],
    workers: int = 4,
) -> dict[str, int]:
    """Append one line per brief to ``out_path``, skipping briefs already done."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = done_brief_ids(out_path)
    todo = [b for b in briefs if b.brief_id not in done]
    lock = threading.Lock()
    stats = {
        "skipped": len(briefs) - len(todo),
        "written": 0,
        "gen_errors": 0,
        "review_errors": 0,
    }
    with (
        out_path.open("a", encoding="utf-8") as fh,
        ThreadPoolExecutor(max_workers=max(1, workers)) as pool,
    ):
        futures = {
            pool.submit(generate_one, b, gen, rev, vars, policies): b for b in todo
        }
        for fut in as_completed(futures):
            row = fut.result()
            with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                stats["written"] += 1
                stats["gen_errors"] += bool(row["gen_error"])
                stats["review_errors"] += bool(
                    isinstance(row["review"], dict) and "error" in row["review"]
                )
            print(
                f"[v8-generate] {futures[fut].brief_id} "
                f"{'ERROR ' + row['gen_error'][:120] if row['gen_error'] else 'ok'}",
                flush=True,
            )
    return stats
