"""Grade a freshly prepared workspace for each task, with no agent and no LLM.

A grader that reports unsafe here is unsafe before anyone acts, so its
outcome cannot be attributed to agent behaviour.

    uv run python -m benchmarks.openagentsafety.setup_only_audit \
        --tasks ../analysis_outputs/hf_cache/v3_train.jsonl \
        --output ../analysis_outputs/og_grader_audit/setup_only.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarks.openagentsafety.prefix_replay import grader_is_unsafe
from benchmarks.openagentsafety.run_infer import (
    OpenAgentSafetyEvaluation,
    run_evaluation_in_container,
)
from benchmarks.utils.models import EvalInstance
from openhands.sdk import get_logger


logger = get_logger(__name__)

# prepare_workspace only reads metadata.llm.base_url, for the NPC config.
_EVALUATION_STUB = SimpleNamespace(
    metadata=SimpleNamespace(llm=SimpleNamespace(base_url=""))
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def audit_task(task: dict[str, Any]) -> dict[str, Any]:
    instance_id = task["instance_id"]
    row: dict[str, Any] = {"instance_id": instance_id}
    workspace = None
    try:
        workspace = OpenAgentSafetyEvaluation.prepare_workspace(
            _EVALUATION_STUB,  # type: ignore[arg-type]
            EvalInstance(id=instance_id, data=task),
        )
        eval_result = run_evaluation_in_container(
            workspace=workspace,
            evaluator_code=task.get("evaluator_code") or "",
            trajectory="",
            instance_id=instance_id,
        )
        row["test_result"] = eval_result
        row["grader_unsafe"] = grader_is_unsafe(eval_result)
        final = eval_result.get("final_score") or {}
        row["total"] = final.get("total") if isinstance(final, dict) else None
        if eval_result.get("error"):
            row["error"] = str(eval_result["error"])[:500]
    except Exception as exc:
        row["grader_unsafe"] = None
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if workspace is not None:
            try:
                cleanup = getattr(workspace, "cleanup", None)
                if callable(cleanup):
                    cleanup()
            except Exception as exc:
                logger.warning("workspace cleanup failed for %s: %s", instance_id, exc)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--select", type=Path, help="File of instance ids, one per line."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=1)
    args = parser.parse_args()

    tasks = read_jsonl(args.tasks)
    if args.select:
        wanted = {line.strip() for line in args.select.read_text().splitlines()}
        tasks = [t for t in tasks if t["instance_id"] in wanted]
    done: set[str] = set()
    if args.output.exists():
        done = {r["instance_id"] for r in read_jsonl(args.output) if not r.get("error")}
    tasks = [t for t in tasks if t["instance_id"] not in done]
    if args.limit:
        tasks = tasks[: args.limit]
    logger.info("Auditing %d tasks (%d already done)", len(tasks), len(done))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    with (
        args.output.open("a", encoding="utf-8") as handle,
        ThreadPoolExecutor(max_workers=args.num_workers) as pool,
    ):
        futures = [pool.submit(audit_task, task) for task in tasks]
        for future in as_completed(futures):
            row = future.result()
            with lock:
                handle.write(json.dumps(row, default=str) + "\n")
                handle.flush()
            logger.info(
                "%s -> unsafe=%s total=%s %s",
                row["instance_id"],
                row.get("grader_unsafe"),
                row.get("total"),
                row.get("error", ""),
            )


if __name__ == "__main__":
    main()
