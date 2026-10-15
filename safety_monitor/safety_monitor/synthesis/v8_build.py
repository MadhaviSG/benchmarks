"""Gate v8 candidates, materialize the accepted ones, and write the HF-schema split."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v8_convert import candidate_to_bundle, materialize_bundle
from safety_monitor.synthesis.v8_gates import GateContext, load_context, run_gates


def _latest_rows(generated_path: Path) -> list[dict[str, Any]]:
    """Last line per brief_id, in first-seen order."""
    rows: dict[str, dict[str, Any]] = {}
    for line in Path(generated_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["brief"]["brief_id"]] = row
    return sorted(rows.values(), key=lambda r: r["brief"]["brief_id"])


def _review_summary(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if review is None:
        return None
    if "error" in review and "runnable" not in review:
        return {"error": review["error"]}
    checks = review.get("checks") or []
    return {
        "runnable": review.get("runnable"),
        "failed_checks": [
            c.get("name") or c.get("id")
            for c in checks
            if str(c.get("status")).upper() == "FAIL"
        ],
        "blocking_issues": review.get("blocking_issues") or [],
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def build(
    generated_path: Path,
    ws_root: Path,
    out_dir: Path,
    *,
    ctx: GateContext | None = None,
    require_review: bool = True,
) -> dict[str, Any]:
    ctx = ctx or load_context()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train, meta, rejects = [], [], []
    seen_ids: set[str] = set()
    fail_counts: Counter[str] = Counter()
    n_candidates = n_gen_errors = 0
    for row in _latest_rows(generated_path):
        brief = row["brief"]
        cand = row.get("candidate")
        if not cand:
            n_gen_errors += 1
            continue
        n_candidates += 1
        try:
            bundle = candidate_to_bundle(cand)
        except Exception as exc:
            result = {
                "passed": False,
                "failures": [f"convert: {type(exc).__name__}: {exc}"],
                "failed_gates": ["convert"],
            }
            bundle = None
        else:
            result = run_gates(
                bundle, row.get("review"), ctx, require_review=require_review
            )
            if bundle["instance_id"] in seen_ids:
                result["passed"] = False
                result["failures"].append(
                    f"duplicate: instance_id {bundle['instance_id']} already accepted"
                )
                result["failed_gates"].append("duplicate")
        fail_counts.update(result["failed_gates"])
        if not result["passed"]:
            rejects.append(
                {"brief_id": brief["brief_id"], "failures": result["failures"]}
            )
            continue
        assert bundle is not None
        materialize_bundle(bundle, ws_root)
        seen_ids.add(bundle["instance_id"])
        ctx.accepted.append(bundle["task_md"])
        train.append(bundle["record"])
        meta.append(
            {
                "instance_id": bundle["instance_id"],
                "brief": brief,
                "gate": result,
                "review": _review_summary(row.get("review")),
                "unsafe_outcome_state": cand.get("unsafe_outcome_state"),
                "safety_boundary": cand.get("safety_boundary"),
                "governing_policy_location": cand.get("governing_policy_location"),
                "coverage": cand.get("coverage"),
            }
        )
    _write_jsonl(out_dir / "v8_train.jsonl", train)
    _write_jsonl(out_dir / "v8_meta.jsonl", meta)
    _write_jsonl(out_dir / "v8_rejects.jsonl", rejects)
    (out_dir / "v8_ids.txt").write_text(
        "".join(r["instance_id"] + "\n" for r in train), encoding="utf-8"
    )
    summary = {
        "candidates": n_candidates,
        "gen_errors": n_gen_errors,
        "accepted": len(train),
        "rejected": len(rejects),
        "rejected_by_gate": dict(fail_counts),
    }
    print(
        f"[v8-build] {len(train)}/{n_candidates} accepted "
        f"({n_gen_errors} generation errors); failures by gate: {dict(fail_counts) or '{}'}"
    )
    return summary
