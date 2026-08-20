"""Render validated v6 seeds into tasks and contrastive trajectory pairs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.pairs import ContrastPair, MinedTrajectory
from safety_monitor.synthesis.v5_generate import (
    GenerationError,
    RenderedPair,
    build_pair,
    build_pair_record,
    build_task_record,
    build_trajectory,
    leakage_report,
    mean_pairwise_jaccard,
    replay_grader,
    schedule,
    validate_pair,
)
from safety_monitor.synthesis.v5_graders import is_no_op, synthesize_evaluator
from safety_monitor.synthesis.v5_types import V5Seed
from safety_monitor.synthesis.v6_bootstrap import (
    bootstrap_grid_fill,
    bootstrap_priority_batches,
)
from safety_monitor.synthesis.v6_decontam import decontamination_report
from safety_monitor.synthesis.v6_splits import build_split_manifest
from safety_monitor.synthesis.v6_types import (
    load_json_seeds,
    seed_to_dict,
    validate_v6_all,
)
from safety_monitor.synthesis.v6_validate import (
    load_existing_corpus_problems,
    summarise_reports,
    validate_seed_gates,
)


RUN = "v6_synthetic"
GENERATOR = "v6_generate"
CORPUS = "v6"

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PAIRS_DIR = _REPO_ROOT / "analysis_outputs" / "v6_synthetic_pairs"
DEFAULT_TASKS_DIR = _REPO_ROOT / "analysis_outputs" / "v6_generated_tasks"
DEFAULT_ACCEPTED = DEFAULT_TASKS_DIR / "accepted_seeds.jsonl"


def _load_content_modules() -> list[V5Seed]:
    """Hand-authored v6 content modules (v6_content_a …), same pattern as v5."""
    import importlib

    seeds: list[V5Seed] = []
    for suffix in "abcdefg":
        try:
            module = importlib.import_module(
                f"safety_monitor.synthesis.v6_content_{suffix}"
            )
        except ModuleNotFoundError:
            continue
        seeds.extend(module.SEEDS)
    return seeds


def load_seeds(
    *,
    include_bootstrap: bool = True,
    include_content: bool = True,
    grid_fill: int = 0,
    accepted_path: Path = DEFAULT_ACCEPTED,
) -> list[V5Seed]:
    seeds: list[V5Seed] = []
    if include_content:
        seeds.extend(_load_content_modules())
    if include_bootstrap:
        seeds.extend(bootstrap_priority_batches())
    if grid_fill:
        seeds.extend(bootstrap_grid_fill(max_new=grid_fill))
    if accepted_path.exists():
        seeds.extend(load_json_seeds(str(accepted_path)))
    # dedupe by instance_id, keep last (content modules beat bootstrap clones)
    by_id: dict[str, V5Seed] = {}
    for seed in sorted(seeds, key=lambda s: s.seed_id):
        by_id[seed.instance_id] = seed
    return sorted(by_id.values(), key=lambda s: s.instance_id)


def _legacy_record(
    rp: RenderedPair, role: str, traj: MinedTrajectory, text: str
) -> dict[str, Any]:
    harmful = role == "harmful"
    return {
        "instance_id": rp.seed.instance_id,
        "label": "unsafe" if harmful else "safe",
        "llm_label": 2 if harmful else 0,
        "source": "synthetic",
        "corpus": CORPUS,
        "scenario": "v6 synthetic",
        "model": "constructed",
        "unsafe_behavior": rp.seed.checkpoint_text,
        "instruction": rp.instruction,
        "trajectory_text": text,
        "dataset": CORPUS,
        "pair_id": f"{RUN}::{rp.seed.instance_id}",
        "pair_role": role,
        "generator": GENERATOR,
        "harm_kind": rp.seed.harm_kind,
        "seed_id": rp.seed.seed_id,
        "outcome_category": rp.seed.outcome_category,
        "mechanism_category": rp.seed.mechanism_category,
        "intent": rp.seed.intent,
        "high_unsafe_count": traj.high_unsafe_count,
        "action_labels": [
            {"tool": s.tool_name, "summary": s.summary or "", "label": s.label.value}
            for s in traj.steps
        ],
    }


def _grid_coverage(seeds: list[V5Seed]) -> dict[str, int]:
    cells: Counter[str] = Counter()
    for seed in seeds:
        svc = seed.services[0] if seed.services else "filesystem-only"
        cells[f"{seed.outcome_category} | {seed.mechanism_category} | {svc}"] += 1
    return dict(sorted(cells.items()))


def _model_json(model: Any) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json()


def generate(
    pairs_dir: str | Path = DEFAULT_PAIRS_DIR,
    tasks_dir: str | Path = DEFAULT_TASKS_DIR,
    *,
    include_bootstrap: bool = True,
    include_content: bool = True,
    grid_fill: int = 0,
    max_tasks: int | None = 550,
    write_report: bool = True,
    strict: bool = True,
) -> dict[str, Any]:
    existing = load_existing_corpus_problems()
    raw_seeds = load_seeds(
        include_bootstrap=include_bootstrap,
        include_content=include_content,
        grid_fill=grid_fill,
    )
    reports: list = []
    seeds: list[V5Seed] = []
    for candidate in raw_seeds:
        report = validate_seed_gates(candidate, existing_problems=existing)
        reports.append(report)
        if report.accepted:
            seeds.append(candidate)
            existing.append(
                candidate.title
                + " "
                + " ".join(candidate.context)
                + " "
                + candidate.seed_sentence
            )
            if max_tasks is not None and len(seeds) >= max_tasks:
                break
    summary_reject = summarise_reports(reports)
    if strict:
        problems = validate_v6_all(seeds)
        if problems:
            raise GenerationError(
                "v6 contract violations:\n" + "\n".join(problems[:20])
            )
    if not seeds:
        raise GenerationError("no accepted v6 seeds to render")

    plan = schedule(seeds)
    pairs_dir = Path(pairs_dir)
    tasks_dir = Path(tasks_dir)
    pairs_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[RenderedPair] = []
    task_records: list[dict[str, Any]] = []
    trajectories: list[MinedTrajectory] = []
    contrast_pairs: list[ContrastPair] = []
    legacy: list[dict[str, Any]] = []
    replays: list[dict[str, Any]] = []
    split_manifest = build_split_manifest(seeds)

    for seed in seeds:
        variant, target = plan[seed.instance_id]
        rp = build_pair(seed, variant, target)
        validate_pair(rp)
        rendered.append(rp)
        evaluator_code = synthesize_evaluator(seed)
        if is_no_op(evaluator_code):
            raise GenerationError(f"{seed.instance_id}: synthesised grader is a no-op")
        task_records.append(
            build_task_record(seed, rp.problem_statement, evaluator_code)
        )
        replay = replay_grader(seed, rp, evaluator_code)
        replays.append(replay)
        if strict and not replay["ok"]:
            raise GenerationError(f"{seed.instance_id}: grader replay failed {replay}")
        split = split_manifest["assignments"][seed.instance_id]
        harmful = build_trajectory(rp, "harmful", split)
        safe = build_trajectory(rp, "clean", split)
        # patch run tag to v6
        harmful.run = RUN
        harmful.key = harmful.key.replace("v5_synthetic", RUN)
        safe.run = RUN
        safe.key = safe.key.replace("v5_synthetic", RUN)
        trajectories += [harmful, safe]
        pair = build_pair_record(rp, harmful, safe)
        pair.pair_id = pair.pair_id.replace("v5_synthetic", RUN)
        contrast_pairs.append(pair)
        legacy.append(_legacy_record(rp, "harmful", harmful, rp.unsafe_text))
        legacy.append(_legacy_record(rp, "clean", safe, rp.safe_text))

    tasks_path = tasks_dir / "v6_train.jsonl"
    _write_jsonl(tasks_path, task_records)
    accepted_path = tasks_dir / "accepted_seeds.jsonl"
    _write_jsonl(accepted_path, [seed_to_dict(seed) for seed in seeds])
    trajectories_path = pairs_dir / "trajectories.jsonl"
    with trajectories_path.open("w", encoding="utf-8") as sink:
        for traj in trajectories:
            payload = json.loads(_model_json(traj))
            payload["corpus"] = CORPUS
            sink.write(json.dumps(payload, ensure_ascii=False) + "\n")
    pairs_path = pairs_dir / "pairs.jsonl"
    with pairs_path.open("w", encoding="utf-8") as sink:
        for pair in contrast_pairs:
            payload = json.loads(_model_json(pair))
            payload["corpus"] = CORPUS
            sink.write(json.dumps(payload, ensure_ascii=False) + "\n")
    legacy_path = pairs_dir / "safety_trajectories_v6_synthetic.jsonl"
    _write_jsonl(legacy_path, legacy)

    jaccard = mean_pairwise_jaccard([r["problem_statement"] for r in task_records])
    decontam = decontamination_report(rendered)
    summary = {
        "corpus": CORPUS,
        "n_tasks": len(task_records),
        "n_pairs": len(contrast_pairs),
        "n_trajectories": len(trajectories),
        "mean_pairwise_jaccard_problem_statements": round(jaccard, 4),
        "grid_cells_filled": _grid_coverage(seeds),
        "validation": summary_reject,
        "splits": split_manifest,
        "decontamination": decontam,
        "replay": {
            "n_ok": sum(1 for r in replays if r["ok"]),
            "n_failed": sum(1 for r in replays if not r["ok"]),
        },
        "leakage": leakage_report(rendered),
        "outputs": {
            "tasks": str(tasks_path),
            "trajectories": str(trajectories_path),
            "pairs": str(pairs_path),
            "legacy": str(legacy_path),
            "manifest": str(pairs_dir / "manifest.json"),
        },
    }
    manifest = {
        "corpus": CORPUS,
        "counts": {
            "tasks": len(seeds),
            "trajectories": len(trajectories),
            "pairs": len(contrast_pairs),
        },
        "splits": split_manifest,
        "outputs": summary["outputs"],
    }
    (pairs_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (pairs_dir / "stats.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    if write_report:
        report_path = pairs_dir / "report.md"
        report_path.write_text(_build_report(seeds, summary), encoding="utf-8")
        summary["outputs"]["report"] = str(report_path)
    progress = pairs_dir / "progress.md"
    progress.write_text(_progress_note(seeds, summary), encoding="utf-8")
    return summary


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_report(seeds: list[V5Seed], summary: dict[str, Any]) -> str:
    lines = [
        "# v6 synthetic contrastive corpus",
        "",
        f"Generated **{summary['n_tasks']} tasks** / **{summary['n_trajectories']} trajectories** "
        f"(corpus `{CORPUS}`).",
        "",
        f"Mean within-set Jaccard (problem statements): **{summary['mean_pairwise_jaccard_problem_statements']}**.",
        "",
        "## Validation",
        "",
        f"- Acceptance rate: {summary['validation']['acceptance_rate']}",
        f"- Rejection by gate: `{summary['validation']['rejection_by_gate']}`",
        "",
        "## Decontamination",
        "",
        f"- Flagged n-grams (n≥6, >3 tasks): **{summary['decontamination']['n_flagged_ngrams']}**",
        "",
        "## Resume",
        "",
        "Deterministic v4/v5 methodology: hand-authored seeds in `v6_content_*.py`,",
        "render via `v5_generate` (shared prefix, pivotal action, grader replay). No LLM calls.",
        "",
        "```bash",
        "cd benchmarks/safety_monitor && PYTHONPATH=. python3 -m safety_monitor v6-synth \\",
        "  --grid-fill 30 --merge",
        "```",
        "",
        "Optional future work: `v6_author.py` can LLM-draft seeds, but it is not required",
        "for synthetic pairs — the deterministic content modules are the primary path.",
        "",
    ]
    return "\n".join(lines)


def _progress_note(seeds: list[V5Seed], summary: dict[str, Any]) -> str:
    return (
        f"# v6 progress\n\n"
        f"- Shipped: **{summary['n_tasks']} tasks** ({summary['n_trajectories']} trajectories)\n"
        f"- Target: **550 v6 tasks** / 1100 trajectories\n"
        f"- Remaining to 550: **{max(0, 550 - summary['n_tasks'])}** tasks\n"
        f"- Mean Jaccard: {summary['mean_pairwise_jaccard_problem_statements']}\n"
        f"- Grid cells filled: {len(summary['grid_cells_filled'])}\n"
        f"- Rejection by gate: {summary['validation']['rejection_by_gate']}\n\n"
        f"**Methodology:** deterministic v4/v5 path — hand-authored `v6_content_*.py` seeds, "
        f"rendered with `v5_generate` (shared prefix, single pivot, grader replay). "
        f"**Zero LLM calls** for seed or trajectory drafting.\n\n"
        f"Regenerate:\n\n"
        f"```bash\n"
        f"cd benchmarks/safety_monitor && PYTHONPATH=. python3 -m safety_monitor v6-synth \\\n"
        f"  --grid-fill 30 --merge\n"
        f"```\n\n"
        f"Optional future work: LLM drafting via `v6-author` is not required for synthetic pairs.\n"
    )
