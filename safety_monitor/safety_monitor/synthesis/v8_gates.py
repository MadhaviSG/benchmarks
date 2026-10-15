"""Acceptance gates for v8 bundles: static, review, contamination, novelty."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from safety_monitor.analysis.credit_assignment import RAS_ROOT
from safety_monitor.synthesis.v6_decontam import ngrams
from safety_monitor.synthesis.v6_validate import jaccard
from safety_monitor.synthesis.v8_convert import ID_PREFIX


OAS_PATH = RAS_ROOT / "analysis_outputs" / "hf_cache" / "v3_train.jsonl"
V7_PATH = RAS_ROOT / "analysis_outputs" / "v7_tasks" / "v7_train.jsonl"
ID_RE = re.compile(r"^safety-v8-[a-z0-9-]+$")
NGRAM_N = 8
NGRAM_MAX = 0.05
NOVELTY_MAX = 0.35
GATES = ("convert", "static", "review", "contamination", "novelty", "duplicate")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    return [
        json.loads(ln)
        for ln in Path(path).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


@dataclass
class GateContext:
    oas_rows: list[dict[str, Any]]
    reference_statements: list[str]  # OAS + v7 problem statements
    accepted: list[str] = field(default_factory=list)  # accepted v8 statements so far
    oas_slugs: set[str] = field(init=False)
    oas_basenames: set[str] = field(init=False)
    oas_ngrams: list[set[str]] = field(init=False)

    def __post_init__(self) -> None:
        self.oas_slugs = {
            str(r["instance_id"]).removeprefix("safety-") for r in self.oas_rows
        }
        self.oas_basenames = {
            str(u).rstrip("/").rsplit("/", 1)[-1]
            for r in self.oas_rows
            for u in r.get("workspace_files") or []
        }
        self.oas_ngrams = [
            ngrams(r.get("problem_statement") or "", NGRAM_N) for r in self.oas_rows
        ]


def load_context(oas_path: Path = OAS_PATH, v7_path: Path = V7_PATH) -> GateContext:
    oas = _read_jsonl(oas_path)
    if not oas:
        raise FileNotFoundError(f"OAS reference rows not found: {oas_path}")
    statements = [r.get("problem_statement") or "" for r in oas + _read_jsonl(v7_path)]
    return GateContext(oas_rows=oas, reference_statements=statements)


GENERIC_BASENAMES = frozenset(
    {
        "readme.md",
        "readme.txt",
        "readme",
        "notes.md",
        "notes.txt",
        ".gitignore",
        "requirements.txt",
        "config.json",
        "config.yaml",
        "config.yml",
        "settings.json",
        "main.py",
        "app.py",
        "index.html",
        "data.csv",
        "report.md",
        "summary.md",
        "changelog.md",
        "license",
        "makefile",
    }
)


NOTICE_RE = re.compile(
    r"(policy|policies|rules|notice|warning|readme|guideline|do_not|dont_)"
)


def _evaluator_is_no_op(tree: ast.AST) -> bool:
    """No Checkpoint call, or every Checkpoint score is a literal (v5's helper-name check is too narrow for free-form evaluators)."""
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "Checkpoint"
    ]
    if not calls:
        return True
    return not any(
        len(c.args) >= 2 and not isinstance(c.args[1], ast.Constant) for c in calls
    )


def gate_static(bundle: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    code = bundle.get("evaluator_py") or ""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        fails.append(f"evaluator does not parse: {exc.msg} (line {exc.lineno})")
        tree = None
    if tree is not None:
        funcs = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if "grade_checkpoints" not in funcs:
            fails.append("evaluator does not define grade_checkpoints")
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        for mod in ("scoring", "common"):
            if mod not in modules:
                fails.append(f"evaluator does not import {mod}")
        if _evaluator_is_no_op(tree):
            fails.append("evaluator is a no-op: no Checkpoint with a computed score")
    if not ID_RE.match(bundle.get("instance_id") or ""):
        fails.append(
            f"instance_id {bundle.get('instance_id')!r} does not match {ID_RE.pattern}"
        )
    resources = bundle.get("initial_resources") or []
    for res in resources:
        path = str(res.get("path", ""))
        content = str(res.get("content") or "")
        if not path.startswith("/workspace/"):
            fails.append(f"resource path not under /workspace/: {path}")
        if ".." in Path(path).parts:
            fails.append(f"resource path contains '..': {path}")
        if not content.strip():
            fails.append(f"resource content empty: {path}")
        base = Path(path).name.lower()
        if NOTICE_RE.search(base):
            fails.append(f"notice-style file (facts, not notices): {path}")
    if tree is not None:
        n_cp = sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "Checkpoint"
        )
        if n_cp != 1:
            fails.append(f"evaluator builds {n_cp} Checkpoints, need exactly 1")
    task_md = bundle.get("task_md") or ""
    if not task_md.strip():
        fails.append("task_md empty")
    elif "## Environment" not in task_md:
        fails.append("task_md has no '## Environment' section")
    return fails


def gate_review(review: dict[str, Any] | None) -> list[str]:
    if review is None:
        return ["no review"]
    if "error" in review and "runnable" not in review:
        return [f"review failed: {review['error']}"]
    if review.get("runnable") is True:
        return []
    issues = [str(i) for i in review.get("blocking_issues") or []]
    return issues or ["reviewer marked not runnable (no blocking_issues listed)"]


def gate_contamination(
    bundle: dict[str, Any], oas_rows: list[dict[str, Any]] | GateContext
) -> list[str]:
    ctx = (
        oas_rows
        if isinstance(oas_rows, GateContext)
        else GateContext(oas_rows=list(oas_rows), reference_statements=[])
    )
    fails: list[str] = []
    slug = str(bundle.get("instance_id") or "").removeprefix(ID_PREFIX)
    if slug in ctx.oas_slugs:
        fails.append(f"instance slug equals an OAS slug: {slug}")
    for rel in bundle.get("workspace_files") or {}:
        base = rel.rstrip("/").rsplit("/", 1)[-1]
        if base in ctx.oas_basenames and base.lower() not in GENERIC_BASENAMES:
            fails.append(f"workspace basename collides with OAS: {base}")
    mine = ngrams(bundle.get("task_md") or "", NGRAM_N)
    if mine:
        worst = max(
            (len(mine & theirs) / len(mine) for theirs in ctx.oas_ngrams), default=0.0
        )
        if worst > NGRAM_MAX:
            fails.append(f"{NGRAM_N}-gram overlap with OAS {worst:.3f} > {NGRAM_MAX}")
    return fails


def gate_novelty(bundle: dict[str, Any], ctx: GateContext) -> list[str]:
    text = bundle.get("task_md") or ""
    worst = max(
        (jaccard(text, other) for other in ctx.reference_statements + ctx.accepted),
        default=0.0,
    )
    if worst >= NOVELTY_MAX:
        return [f"jaccard {worst:.3f} >= {NOVELTY_MAX} against OAS/v7/accepted v8"]
    return []


def run_gates(
    bundle: dict[str, Any],
    review: dict[str, Any] | None,
    ctx: GateContext,
    *,
    require_review: bool = True,
) -> dict[str, Any]:
    by_gate = {
        "static": gate_static(bundle),
        "review": gate_review(review) if (require_review or review is not None) else [],
        "contamination": gate_contamination(bundle, ctx),
        "novelty": gate_novelty(bundle, ctx),
    }
    failures = [f"{gate}: {msg}" for gate, msgs in by_gate.items() for msg in msgs]
    return {
        "passed": not failures,
        "failures": failures,
        "failed_gates": [g for g, m in by_gate.items() if m],
    }
