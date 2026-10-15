"""CLI entrypoint for safety_monitor synthesis and SFT commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_v5_synth(_: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v5_generate import generate

    summary = generate()
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_v7_build(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v7_build import build

    summary = build(
        tasks_path=args.tasks,
        ws_root=args.ws_root,
        out_dir=args.out,
        strict=not args.loose,
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed"] else 0


def _cmd_v6_synth(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v6_generate import generate

    summary = generate(
        grid_fill=args.grid_fill,
        include_bootstrap=not args.no_bootstrap,
        include_content=not args.no_content,
        strict=not args.loose,
    )
    print(json.dumps(summary, indent=2))
    if args.merge:
        from safety_monitor.synthesis.v6_merge import merge_all

        print(json.dumps(merge_all(), indent=2))
    return 0


def _cmd_v6_author(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v6_author import author_batch, load_llm_config
    from safety_monitor.synthesis.v6_bootstrap import (
        bootstrap_grid_fill,
        bootstrap_priority_batches,
    )

    cfg = load_llm_config(args.config)
    specs = []
    if args.priority:
        for seed in bootstrap_priority_batches():
            specs.append(
                {
                    "seed_id": seed.seed_id,
                    "outcome": seed.outcome_category,
                    "mechanism": seed.mechanism_category,
                    "seed_sentence": seed.seed_sentence,
                    "services": seed.services,
                    "grader_kind": seed.grader.kind,
                    "artifact": seed.grader.target,
                    "tool_realization": seed.tool_realization,
                    "instance_slug": seed.instance_id,
                }
            )
    if args.grid_fill:
        for seed in bootstrap_grid_fill(max_new=args.grid_fill):
            specs.append(
                {
                    "seed_id": seed.seed_id,
                    "outcome": seed.outcome_category,
                    "mechanism": seed.mechanism_category,
                    "seed_sentence": seed.seed_sentence,
                    "services": seed.services,
                    "grader_kind": seed.grader.kind,
                    "artifact": seed.grader.target,
                    "tool_realization": seed.tool_realization,
                    "instance_slug": seed.instance_id,
                }
            )
    specs = specs[: args.batch_size]
    if not specs:
        print("No specs to draft.", file=sys.stderr)
        return 1
    accepted, reports = author_batch(specs, cfg=cfg)
    print(
        json.dumps(
            {
                "n_specs": len(specs),
                "n_accepted": len(accepted),
                "n_rejected": len(reports) - len(accepted),
                "accepted_ids": [s.instance_id for s in accepted],
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m safety_monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    p7 = sub.add_parser(
        "v7-build", help="Build hand-authored v7 tasks (validate + write HF records)"
    )
    p7.add_argument(
        "--tasks", required=True, type=Path, help="Hand-authored tasks.jsonl"
    )
    p7.add_argument(
        "--ws-root", required=True, type=Path, dest="ws_root", help="Workspace root"
    )
    p7.add_argument("--out", required=True, type=Path, help="Output directory")
    p7.add_argument(
        "--loose", action="store_true", help="Warn instead of fail on check_task errors"
    )
    p7.set_defaults(func=_cmd_v7_build)

    p5 = sub.add_parser("v5-synth", help="Render v5 synthetic pairs (deterministic)")
    p5.set_defaults(func=_cmd_v5_synth)

    p6 = sub.add_parser("v6-synth", help="Render v6 synthetic pairs")
    p6.add_argument(
        "--grid-fill", type=int, default=25, help="Extra grid-fill bootstrap seeds"
    )
    p6.add_argument(
        "--no-bootstrap", action="store_true", help="Skip priority bootstrap batches"
    )
    p6.add_argument(
        "--no-content",
        action="store_true",
        help="Skip hand-authored v6_content_* modules",
    )
    p6.add_argument(
        "--loose", action="store_true", help="Do not fail on first validation error"
    )
    p6.add_argument(
        "--merge", action="store_true", help="Also write synthetic_pairs_v2 merge"
    )
    p6.set_defaults(func=_cmd_v6_synth)

    pa = sub.add_parser("v6-author", help="LLM-draft v6 seeds with validation gates")
    pa.add_argument(
        "--config", type=Path, default=None, help="Path to .llm_config JSON"
    )
    pa.add_argument("--batch-size", type=int, default=10)
    pa.add_argument(
        "--target", type=int, default=425, help="Target new tasks (informational)"
    )
    pa.add_argument(
        "--priority", action="store_true", help="Draft priority missing/recovered seeds"
    )
    pa.add_argument("--grid-fill", type=int, default=0)
    pa.set_defaults(func=_cmd_v6_author)

    p_sft = sub.add_parser(
        "sft-run",
        help="Single-run critic SFT (Qwen path; alias: sft-qwen)",
    )
    _add_sft_run_args(p_sft)
    p_sft.set_defaults(func=_cmd_sft_run)

    p_qwen = sub.add_parser(
        "sft-qwen",
        help="Alias for sft-run (documented in qwen_sft/report.md)",
    )
    _add_sft_run_args(p_qwen)
    p_qwen.set_defaults(func=_cmd_sft_run)

    p_scale = sub.add_parser(
        "sft-scaling",
        help="ShieldGemma nested data-scaling SFT ladder",
    )
    p_scale.add_argument(
        "--train",
        type=Path,
        nargs="+",
        default=None,
        help="Synthetic trajectory JSONL (default: analysis_outputs/synthetic_pairs)",
    )
    p_scale.add_argument(
        "--eval",
        type=Path,
        default=None,
        help="V3 eval JSONL (default: analysis_outputs/critic_training_pairs)",
    )
    p_scale.add_argument("--out-dir", type=Path, default=None)
    p_scale.add_argument("--backend", choices=("auto", "mock", "hf"), default="auto")
    p_scale.add_argument("--model-path", type=str, default=None)
    p_scale.add_argument(
        "--rungs",
        type=str,
        default="0,200,400,all",
        help="Comma-separated rung sizes; 'all' = every train task",
    )
    p_scale.add_argument("--epochs", type=int, default=2)
    p_scale.add_argument("--seed", type=int, default=42)
    p_scale.add_argument(
        "--smoke",
        action="store_true",
        help="Zero-shot only, first 100 v3 trajectories, print score summary",
    )
    _add_sft_compute_args(p_scale)
    p_scale.set_defaults(func=_cmd_sft_scaling)

    p_online = sub.add_parser(
        "online-replay",
        help="Replay recorded trajectories as an online monitor (CPU, read-only)",
    )
    p_online.add_argument(
        "--trajectories",
        type=Path,
        default=None,
        help="Trajectory JSONL (default: critic_training_pairs)",
    )
    p_online.add_argument("--out-dir", type=Path, default=None)
    p_online.add_argument(
        "--families",
        type=str,
        default="llm_blocking,cygnal",
        help="Run families to replay: llm_blocking, cygnal, passive, other",
    )
    p_online.add_argument(
        "--aggregators",
        type=str,
        default="cummax,noisy_or,ew_decay,logodds_sum",
        help="Credence aggregators to report",
    )
    p_online.add_argument("--max-trajectories", type=int, default=None)
    p_online.set_defaults(func=_cmd_online_replay)

    p_label = sub.add_parser(
        "critic-label",
        help="Label recorded trajectories with a prompted critic (RQ1)",
    )
    p_label.add_argument("--trajectories", type=Path, default=None)
    p_label.add_argument("--out-dir", type=Path, required=True)
    p_label.add_argument(
        "--completer",
        choices=("keyword", "ollama", "shieldgemma"),
        default="ollama",
        help="keyword = offline stand-in; ollama = local server; "
        "shieldgemma = local zero-shot guard model",
    )
    p_label.add_argument(
        "--whole-trajectory",
        action="store_true",
        help="Control: one verdict per run instead of one per action",
    )
    p_label.add_argument(
        "--variant",
        choices=("base", "policy", "criteria"),
        default="base",
        help="policy = told a general harm policy; criteria = also shown the "
        "task's own checkpoint text (upper bound, not deployable)",
    )
    p_label.add_argument("--tasks", type=Path, default=None)
    p_label.add_argument(
        "--tiers", type=Path, default=None, help="Label only tier 'valid' tasks"
    )
    p_label.add_argument("--model-path", type=str, default=None)
    p_label.add_argument("--eval-batch-size", type=int, default=8)
    p_label.add_argument("--ollama-model", type=str, default="qwen3.5:9b")
    p_label.add_argument("--ollama-host", type=str, default="http://127.0.0.1:11434")
    p_label.add_argument(
        "--families",
        type=str,
        default="passive",
        help="Run families to label: passive, llm_blocking, cygnal, other",
    )
    p_label.add_argument("--max-trajectories", type=int, default=None)
    p_label.add_argument("--num-workers", type=int, default=4)
    p_label.set_defaults(func=_cmd_critic_label)

    p_rq1 = sub.add_parser(
        "rq1-report",
        help="Score critic verdicts against rule-based outcomes (RQ1)",
    )
    p_rq1.add_argument("--trajectories", type=Path, default=None)
    p_rq1.add_argument(
        "--verdicts",
        type=Path,
        nargs="+",
        required=True,
        help="One or more critic-label output directories",
    )
    p_rq1.add_argument("--out-dir", type=Path, required=True)
    p_rq1.add_argument("--families", type=str, default="passive")
    p_rq1.add_argument(
        "--tiers",
        type=Path,
        default=None,
        help="task_tiers.jsonl from the grader audit; keeps tier 'valid' only",
    )
    p_rq1.add_argument(
        "--keep-blocked",
        action="store_true",
        help="Keep trajectories a recorded analyzer blocked (censored outcomes)",
    )
    p_rq1.add_argument(
        "--exclude-tasks",
        type=Path,
        default=None,
        help="File of instance ids to leave out (e.g. tasks used to design a prompt)",
    )
    p_rq1.set_defaults(func=_cmd_rq1_report)

    p_rq3 = sub.add_parser(
        "rq3-report",
        help="Compare credence aggregators against the raw flag count (RQ3)",
    )
    p_rq3.add_argument("--trajectories", type=Path, default=None)
    p_rq3.add_argument("--verdicts", type=Path, required=True)
    p_rq3.add_argument("--out-dir", type=Path, required=True)
    p_rq3.add_argument("--families", type=str, default="passive")
    p_rq3.add_argument("--tiers", type=Path, default=None)
    p_rq3.set_defaults(func=_cmd_rq3_report)

    p_credit = sub.add_parser(
        "credit-assign",
        help="LLM/fixture credit assignment over recorded trajectories",
    )
    p_credit.add_argument(
        "--trajectories",
        type=Path,
        default=None,
        help="Trajectory JSONL (default: analysis_outputs/real_rollout_sft)",
    )
    p_credit.add_argument("--out-dir", type=Path, default=None)
    p_credit.add_argument(
        "--completer",
        choices=("fixture", "llm", "ollama"),
        default="fixture",
        help="fixture = no-credit stand-in; llm = litellm; ollama = local server",
    )
    p_credit.add_argument("--llm-config", type=Path, default=None)
    p_credit.add_argument("--ollama-model", type=str, default="qwen3.5:9b")
    p_credit.add_argument("--ollama-host", type=str, default="http://127.0.0.1:11434")
    p_credit.add_argument("--max-trajectories", type=int, default=None)
    p_credit.add_argument(
        "--mixed-sample",
        type=int,
        default=None,
        help="Take a balanced grader-safe/unsafe sample of this size",
    )
    p_credit.add_argument("--sample-seed", type=int, default=0)
    p_credit.set_defaults(func=_cmd_credit_assign)

    p_gate = sub.add_parser(
        "sft-from-labels",
        help="Evaluate the action-level SFT gate; default path does not train",
    )
    p_gate.add_argument(
        "--trajectories",
        type=Path,
        default=None,
        help="Original trajectories used for the sink oracle and task probe",
    )
    p_gate.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="proposed_labels.jsonl from credit-assign",
    )
    p_gate.add_argument("--out-dir", type=Path, default=None)
    p_gate.add_argument(
        "--launch-if-open",
        action="store_true",
        help="Only then consider launching SFT, and only if the gate is open "
        "on real labels. Default: never launch.",
    )
    p_gate.set_defaults(func=_cmd_sft_from_labels)

    p_pair = sub.add_parser(
        "pair-attribution",
        help="LLM picks the harmful step of an unsafe run, with and without a safe run",
    )
    p_pair.add_argument(
        "--models", type=str, default="qwen,sonnet", help="qwen, sonnet"
    )
    p_pair.add_argument("--out-dir", type=Path, default=None)
    p_pair.add_argument("--ollama-model", type=str, default="qwen3.5:9b")
    p_pair.add_argument("--ollama-host", type=str, default="http://127.0.0.1:11434")
    p_pair.add_argument(
        "--llm-config", type=Path, default=None, help="gateway config JSON"
    )
    p_pair.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="manifest JSON of v7/v9 run sets (default: analysis_outputs/pair_attribution/sources.json)",
    )
    p_pair.add_argument(
        "--no-sources", action="store_true", help="OAS and the 4 v7 baseline runs only"
    )
    p_pair.add_argument(
        "--v7-results",
        action="append",
        default=[],
        help="results dir; pair with --v7-tasks",
    )
    p_pair.add_argument(
        "--v7-tasks",
        action="append",
        default=[],
        help="tasks.jsonl for the matching --v7-results",
    )
    p_pair.set_defaults(func=_cmd_pair_attribution)

    p_v8b = sub.add_parser(
        "v8-briefs",
        help="Enumerate v8 coverage briefs over the outcome x mechanism grid",
    )
    p_v8b.add_argument("--n", type=int, default=1000)
    p_v8b.add_argument("--seed", type=int, default=0)
    p_v8b.add_argument(
        "--out", type=Path, default=Path("analysis_outputs/v8_tasks/briefs.jsonl")
    )
    p_v8b.set_defaults(func=_cmd_v8_briefs)

    p_v8g = sub.add_parser(
        "v8-generate", help="Generate and review v8 task candidates (resumable)"
    )
    p_v8g.add_argument(
        "--briefs", type=Path, default=Path("analysis_outputs/v8_tasks/briefs.jsonl")
    )
    p_v8g.add_argument(
        "--out", type=Path, default=Path("analysis_outputs/v8_tasks/generated.jsonl")
    )
    p_v8g.add_argument(
        "--limit", type=int, default=None, help="use only the first N briefs"
    )
    p_v8g.add_argument("--workers", type=int, default=4)
    p_v8g.add_argument(
        "--gen-config",
        type=Path,
        default=Path("benchmarks/.llm_config/ai-gateway-sonnet.json"),
    )
    p_v8g.add_argument("--gen-temperature", type=float, default=0.8)
    p_v8g.add_argument("--gen-max-tokens", type=int, default=32000)
    p_v8g.add_argument(
        "--review-config",
        type=Path,
        default=Path("benchmarks/.llm_config/ai-gateway-flash.json"),
    )
    p_v8g.add_argument("--review-max-tokens", type=int, default=8000)
    p_v8g.add_argument("--no-review", action="store_true")
    p_v8g.set_defaults(func=_cmd_v8_generate)

    p_v8build = sub.add_parser(
        "v8-build", help="Gate v8 candidates and write bundles plus v8_train.jsonl"
    )
    p_v8build.add_argument(
        "--generated",
        type=Path,
        default=Path("analysis_outputs/v8_tasks/generated.jsonl"),
    )
    p_v8build.add_argument(
        "--ws-root", type=Path, default=Path("analysis_outputs/mg_workspaces")
    )
    p_v8build.add_argument(
        "--out", type=Path, default=Path("analysis_outputs/v8_tasks")
    )
    p_v8build.add_argument(
        "--no-review", action="store_true", help="do not require a review to accept"
    )
    p_v8build.set_defaults(func=_cmd_v8_build)

    p_v8a = sub.add_parser("v8-appendix", help="Write the v8 prompts appendix markdown")
    p_v8a.add_argument(
        "--out",
        type=Path,
        default=Path("analysis_outputs/v8_tasks/prompts_appendix.md"),
    )
    p_v8a.set_defaults(func=_cmd_v8_appendix)

    p_v9g = sub.add_parser(
        "v9-generate",
        help="Generate, gate and review v9 tasks in the v7 format (resumable)",
    )
    p_v9g.add_argument(
        "--briefs",
        type=Path,
        default=Path("analysis_outputs/v9_tasks/briefs.jsonl"),
        help="read if present, else sampled with --n/--seed and written here",
    )
    p_v9g.add_argument("--n", type=int, default=40)
    p_v9g.add_argument("--seed", type=int, default=9)
    p_v9g.add_argument(
        "--out-dir", type=Path, default=Path("analysis_outputs/v9_tasks")
    )
    p_v9g.add_argument(
        "--target",
        type=int,
        default=20,
        help="stop once this many tasks pass every gate",
    )
    p_v9g.add_argument("--workers", type=int, default=4)
    p_v9g.add_argument(
        "--gen-config",
        type=Path,
        default=Path("benchmarks/.llm_config/ai-gateway-sonnet.json"),
    )
    p_v9g.add_argument("--gen-temperature", type=float, default=0.7)
    p_v9g.add_argument("--gen-max-tokens", type=int, default=32000)
    p_v9g.add_argument(
        "--review-config",
        type=Path,
        default=Path("benchmarks/.llm_config/ai-gateway-sonnet.json"),
    )
    p_v9g.add_argument("--review-max-tokens", type=int, default=16000)
    p_v9g.add_argument(
        "--max-gen-calls",
        type=int,
        default=60,
        help="cap on uncached generate+revise calls",
    )
    p_v9g.add_argument("--no-review", action="store_true")
    p_v9g.add_argument(
        "--gateway-retries",
        type=int,
        default=2,
        help="HTTP tries per call (504s are retried)",
    )
    p_v9g.add_argument(
        "--sampler",
        choices=("round4", "round5"),
        default="round4",
        help="round4: sample_briefs + round-4 overrides; round5: sample_briefs_weighted "
        "(workspace-only, data_loss/falsification weighted, NPC capped by --max-npc)",
    )
    p_v9g.add_argument(
        "--max-npc",
        type=int,
        default=3,
        help="round5 sampler: at most this many npc_mid_task briefs",
    )
    p_v9g.add_argument(
        "--exclude-domains-from",
        type=Path,
        nargs="*",
        default=[],
        help="round5 sampler: skip domains used by the tasks in these jsonl files",
    )
    p_v9g.add_argument(
        "--novelty-extra",
        type=Path,
        nargs="*",
        default=[],
        help="extra task jsonl files whose statements join the novelty corpus",
    )
    p_v9g.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="per-brief cap on generator calls (generation, retries after a failed call, revisions)",
    )
    p_v9g.add_argument(
        "--stop-after-dry-run",
        type=int,
        default=None,
        help="stop generating once this many tasks pass every gate through the dry run",
    )
    p_v9g.add_argument(
        "--gen-reasoning-effort",
        choices=("low", "medium", "high"),
        default=None,
        help="pass reasoning_effort on generation calls (shorter silent thinking phase, fewer 504s); "
        "appended to the cache key only when set",
    )
    p_v9g.add_argument(
        "--review-reasoning-effort",
        choices=("low", "medium", "high"),
        default=None,
        help="pass reasoning_effort on review calls (appended to the cache key only when set)",
    )
    p_v9g.add_argument(
        "--drops",
        type=Path,
        default=None,
        help="tasks_dropped.jsonl: hand-read drops do not count toward --target or the novelty set",
    )
    p_v9g.add_argument(
        "--no-retry-errors",
        action="store_true",
        help="never re-attempt a brief whose record holds a generation error",
    )
    p_v9g.set_defaults(func=_cmd_v9_generate)

    p_v9b = sub.add_parser(
        "v9-build",
        help="Materialise accepted v9 tasks and write fixture-runner references",
    )
    p_v9b.add_argument(
        "--tasks", type=Path, default=Path("analysis_outputs/v9_tasks/tasks.jsonl")
    )
    p_v9b.add_argument(
        "--ws-root", type=Path, default=Path("analysis_outputs/mg_workspaces")
    )
    p_v9b.add_argument("--out", type=Path, default=Path("analysis_outputs/v9_tasks"))
    p_v9b.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="v8_reference_runs output; keep only tasks that pass (or are fixture_pending)",
    )
    p_v9b.add_argument(
        "--drops",
        type=Path,
        default=None,
        help="hand-read drops (default <out>/tasks_dropped.jsonl when present)",
    )
    p_v9b.add_argument(
        "--stem", default="v9", help="names {stem}_train.jsonl / _meta.jsonl / _ids.txt"
    )
    p_v9b.add_argument(
        "--final-name",
        default="tasks_final.jsonl",
        help="fixture-passing tasks file name",
    )
    p_v9b.add_argument(
        "--references-name",
        default="v9_references.jsonl",
        help="runner references file name",
    )
    p_v9b.set_defaults(func=_cmd_v9_build)

    args = parser.parse_args(argv)
    return args.func(args)


def _ras_path(path: Path) -> Path:
    """Relative CLI paths resolve against the RAS repo root."""
    from safety_monitor.analysis.credit_assignment import RAS_ROOT

    return path if path.is_absolute() else RAS_ROOT / path


def _cmd_v8_briefs(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v8_briefs import enumerate_briefs, write_briefs
    from safety_monitor.synthesis.v8_prompts import load_seed_topics, load_vars

    briefs = enumerate_briefs(load_vars(), load_seed_topics(), args.n, args.seed)
    out = _ras_path(args.out)
    write_briefs(briefs, out)
    print(f"wrote {len(briefs)} briefs to {out}")
    return 0


def _v8_completer(config: Path, *, temperature: float, max_tokens: int):
    from safety_monitor.analysis.credit_assignment import RAS_ROOT
    from safety_monitor.analysis.pair_attribution import make_gateway_completer

    cfg = json.loads(config.read_text(encoding="utf-8"))
    model = str(cfg["model"]).removeprefix("litellm_proxy/").replace("/", "_")
    return make_gateway_completer(
        config,
        cache_dir=RAS_ROOT / "analysis_outputs" / "v8_tasks" / "cache" / model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _cmd_v8_generate(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v8_briefs import read_briefs
    from safety_monitor.synthesis.v8_generate import generate_batch
    from safety_monitor.synthesis.v8_prompts import load_policies, load_vars

    vars_ = load_vars()
    policies = load_policies(required=vars_.outcomes)
    briefs = read_briefs(_ras_path(args.briefs))
    if args.limit is not None:
        briefs = briefs[: args.limit]
    gen = _v8_completer(
        _ras_path(args.gen_config),
        temperature=args.gen_temperature,
        max_tokens=args.gen_max_tokens,
    )
    rev = None
    if not args.no_review:
        rev = _v8_completer(
            _ras_path(args.review_config),
            temperature=0.0,
            max_tokens=args.review_max_tokens,
        )
    stats = generate_batch(
        briefs, _ras_path(args.out), gen, rev, vars_, policies, workers=args.workers
    )
    print(json.dumps(stats, indent=2))
    return 0


def _cmd_v8_build(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v8_build import build

    summary = build(
        _ras_path(args.generated),
        _ras_path(args.ws_root),
        _ras_path(args.out),
        require_review=not args.no_review,
    )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_v8_appendix(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v8_prompts import render_appendix

    print(f"wrote {render_appendix(_ras_path(args.out))}")
    return 0


def _v9_gateway(
    config: Path,
    *,
    temperature: float,
    max_tokens: int,
    retries: int = 2,
    reasoning_effort: str | None = None,
):
    """Streamed gateway completer cached under v9_tasks/cache, plus a cache-hit predicate for the call budget."""
    from safety_monitor.analysis.credit_assignment import RAS_ROOT
    from safety_monitor.synthesis.v9_generate import make_streaming_gateway_completer

    cfg = json.loads(config.read_text(encoding="utf-8"))
    model = str(cfg["model"]).removeprefix("litellm_proxy/")
    return make_streaming_gateway_completer(
        config,
        cache_dir=RAS_ROOT
        / "analysis_outputs"
        / "v9_tasks"
        / "cache"
        / model.replace("/", "_"),
        temperature=temperature,
        max_tokens=max_tokens,
        retries=retries,
        reasoning_effort=reasoning_effort,
    )


def _cmd_v9_generate(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v9_generate import (
        Budget,
        apply_overrides,
        load_gate_context,
        read_briefs,
        read_drops,
        read_records,
        run,
        sample_briefs,
        sample_briefs_weighted,
        write_briefs,
    )
    from safety_monitor.synthesis.v9_prompts import (
        load_v7_exemplars,
        render_prompt_appendix,
    )

    briefs_path = _ras_path(args.briefs)
    if briefs_path.exists():
        briefs = read_briefs(briefs_path)
    elif args.sampler == "round5":
        used = {
            str((r.get("axes") or {}).get("domain"))
            for p in args.exclude_domains_from
            for r in read_records(_ras_path(p))
        }
        briefs = sample_briefs_weighted(
            args.n, args.seed, max_npc=args.max_npc, exclude_domains=used
        )
    else:
        briefs = sample_briefs(args.n, args.seed)
    if args.sampler == "round4":
        briefs = apply_overrides(briefs)
    write_briefs(briefs, briefs_path)
    out_dir = _ras_path(args.out_dir)
    exemplars = load_v7_exemplars()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompts.md").write_text(
        render_prompt_appendix(exemplars, briefs[0]), encoding="utf-8"
    )
    gen_raw, gen_cached = _v9_gateway(
        _ras_path(args.gen_config),
        temperature=args.gen_temperature,
        max_tokens=args.gen_max_tokens,
        retries=args.gateway_retries,
        reasoning_effort=args.gen_reasoning_effort,
    )
    gen = Budget(args.max_gen_calls, gen_raw, gen_cached)
    rev = None
    if not args.no_review:
        rev, _ = _v9_gateway(
            _ras_path(args.review_config),
            temperature=0.0,
            max_tokens=args.review_max_tokens,
            retries=args.gateway_retries,
            reasoning_effort=args.review_reasoning_effort,
        )
    ctx = load_gate_context(
        extra_statement_paths=[_ras_path(p) for p in args.novelty_extra]
    )
    stats = run(
        briefs,
        out_dir,
        gen,
        rev,
        ctx=ctx,
        exemplars=exemplars,
        target=args.target,
        workers=args.workers,
        max_attempts=args.max_attempts,
        stop_after_dry_run=args.stop_after_dry_run,
        retry_gen_errors=not args.no_retry_errors,
        dropped_ids=set(read_drops(_ras_path(args.drops)))
        if args.drops
        else frozenset(),
    )
    stats["uncached_generation_calls"] = gen.used
    print(json.dumps(stats, indent=2))
    return 0


def _cmd_v9_build(args: argparse.Namespace) -> int:
    from safety_monitor.synthesis.v9_generate import build_v9

    summary = build_v9(
        _ras_path(args.tasks),
        _ras_path(args.ws_root),
        _ras_path(args.out),
        fixtures=_ras_path(args.fixtures) if args.fixtures else None,
        drops=_ras_path(args.drops) if args.drops else None,
        stem=args.stem,
        final_name=args.final_name,
        references_name=args.references_name,
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["build"]["failed"] else 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _add_sft_compute_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Per-device train batch size (default 1; try 8 on 48GB A6000)",
    )
    parser.add_argument(
        "--grad-accum",
        type=int,
        default=8,
        help="Gradient accumulation steps (default 8; try 1 with --batch-size 8)",
    )
    parser.add_argument(
        "--eval-batch-size",
        type=int,
        default=1,
        help="ShieldGemma eval forward batch size (default 1, sequential)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero if HuggingFace SFT cannot run (no mock fallback)",
    )


def _add_sft_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--train",
        type=Path,
        nargs="+",
        default=None,
        help="Synthetic trajectory JSONL path(s)",
    )
    parser.add_argument(
        "--eval",
        type=Path,
        default=None,
        help="V3 eval trajectory JSONL (critic_training_pairs)",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--backend", choices=("auto", "mock", "hf"), default="auto")
    parser.add_argument("--model-path", type=str, default=None)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument(
        "--family",
        choices=("qwen", "shieldgemma"),
        default="qwen",
        help="Model family: qwen chat SFT or ShieldGemma native Yes/No",
    )
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--max-v3-trajectories", type=int, default=None)
    parser.add_argument(
        "--use-shipped-splits",
        action="store_true",
        help="Use corpus split=train/dev/test instead of the hashed qwen-sft cut",
    )
    _add_sft_compute_args(parser)


def _default_train() -> Path:
    return _repo_root() / "analysis_outputs" / "synthetic_pairs" / "trajectories.jsonl"


def _default_eval() -> Path:
    return (
        _repo_root()
        / "analysis_outputs"
        / "critic_training_pairs"
        / "trajectories.jsonl"
    )


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _cmd_critic_label(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.critic_sweep import CompletionFn, label_corpus

    complete: CompletionFn | None
    label_fn = None
    if args.completer == "ollama":
        from safety_monitor.analysis.credit_assignment import make_ollama_completer

        ollama = make_ollama_completer(
            args.ollama_model, host=args.ollama_host, num_predict=40
        )
        critic_id = f"ollama:{args.ollama_model}"

        def ollama_complete(messages: list[dict[str, str]]) -> str:
            return ollama(messages, None)  # type: ignore[arg-type]

        complete = ollama_complete

    elif args.completer == "shieldgemma":
        from safety_monitor.analysis.critic_sweep import shieldgemma_label_fn
        from safety_monitor.critic.shieldgemma import load_shieldgemma_critic

        complete = None
        label_fn = shieldgemma_label_fn(
            load_shieldgemma_critic(
                model_path=args.model_path, eval_batch_size=args.eval_batch_size
            )
        )
        critic_id = "shieldgemma-2b-zeroshot"
        args.num_workers = 1
    else:
        from safety_monitor.sft.backends import keyword_complete

        complete = keyword_complete
        critic_id = "keyword"

    if args.whole_trajectory:
        from safety_monitor.analysis.critic_sweep import trajectory_judge_label_fn

        if complete is None:
            print("--whole-trajectory needs a prompted completer", file=sys.stderr)
            return 1
        label_fn = trajectory_judge_label_fn(complete)
        complete = None
        critic_id = f"{critic_id}:trajectory-judge"

    if args.variant != "base":
        from safety_monitor.analysis.critic_sweep import variant_label_fn

        if complete is None:
            print("--variant needs a prompted completer", file=sys.stderr)
            return 1
        label_fn = variant_label_fn(complete, args.variant, tasks_path=args.tasks)
        complete = None
        critic_id = f"{critic_id}:{args.variant}"

    summary = label_corpus(
        args.trajectories or _default_eval(),
        args.out_dir,
        tiers_path=args.tiers,
        complete=complete,
        label_fn=label_fn,
        critic_id=critic_id,
        families=_split_csv(args.families),
        max_trajectories=args.max_trajectories,
        num_workers=args.num_workers,
    )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_rq1_report(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.critic_sweep import build_report, format_report

    report = build_report(
        args.trajectories or _default_eval(),
        args.verdicts,
        args.out_dir,
        families=_split_csv(args.families),
        tiers_path=args.tiers,
        drop_blocked=not args.keep_blocked,
        exclude_tasks=(
            args.exclude_tasks.read_text().split() if args.exclude_tasks else ()
        ),
    )
    print(format_report(report))
    return 0


def _cmd_rq3_report(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.critic_sweep import build_rq3_report

    report = build_rq3_report(
        args.trajectories or _default_eval(),
        args.verdicts,
        args.out_dir,
        families=_split_csv(args.families),
        tiers_path=args.tiers,
    )
    print(json.dumps(report, indent=2, default=float))
    return 0


def _cmd_credit_assign(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.credit_assignment import (
        DEFAULT_OUT_DIR,
        DEFAULT_TRAJECTORIES,
        run_credit_assignment,
    )

    result = run_credit_assignment(
        trajectories_path=args.trajectories or DEFAULT_TRAJECTORIES,
        out_dir=args.out_dir or DEFAULT_OUT_DIR,
        completer_kind=args.completer,
        llm_config=args.llm_config,
        max_trajectories=args.max_trajectories,
        mixed_sample=args.mixed_sample,
        sample_seed=args.sample_seed,
        ollama_model=getattr(args, "ollama_model", None),
        ollama_host=getattr(args, "ollama_host", "http://127.0.0.1:11434"),
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def _cmd_pair_attribution(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.pair_attribution import (
        GATEWAY_CONFIG,
        OUT_DIR,
        SOURCES_MANIFEST,
        format_results,
        manifest_from_pairs,
        run_pair_attribution,
    )

    if args.v7_results or args.v7_tasks:
        sources = manifest_from_pairs(args.v7_results, args.v7_tasks)
    elif args.no_sources:
        sources = None
    else:
        sources = args.sources or SOURCES_MANIFEST
    results = run_pair_attribution(
        models=_split_csv(args.models),
        out_dir=args.out_dir or OUT_DIR,
        ollama_model=args.ollama_model,
        ollama_host=args.ollama_host,
        gateway_config=args.llm_config or GATEWAY_CONFIG,
        sources=sources,
    )
    print(format_results(results))
    return 0


def _cmd_sft_from_labels(args: argparse.Namespace) -> int:
    from safety_monitor.analysis.credit_assignment import (
        DEFAULT_OUT_DIR,
        DEFAULT_TRAJECTORIES,
    )
    from safety_monitor.sft.label_gate import run_sft_gate

    result = run_sft_gate(
        trajectories_path=args.trajectories or DEFAULT_TRAJECTORIES,
        labels_path=args.labels,
        out_dir=args.out_dir or DEFAULT_OUT_DIR,
        launch_if_open=bool(args.launch_if_open),
    )
    print(json.dumps(result, indent=2, default=str))
    if result.get("sft_launched"):
        return 0
    # Closed (or open-but-not-launched) is the expected default path.
    return 0


def _cmd_online_replay(args: argparse.Namespace) -> int:
    from safety_monitor.online.replay import main as replay_main

    path = args.trajectories or _default_eval()
    try:
        replay_main(
            path,
            out_dir=args.out_dir,
            families=[f.strip() for f in args.families.split(",") if f.strip()],
            aggregator_names=[
                a.strip() for a in args.aggregators.split(",") if a.strip()
            ],
            max_trajectories=args.max_trajectories,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"online-replay failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_sft_run(args: argparse.Namespace) -> int:
    from safety_monitor.sft.backends import HfUnavailableError
    from safety_monitor.sft.experiment import run_experiment

    train = args.train or [_default_train()]
    eval_path = args.eval or _default_eval()
    out_dir = args.out_dir or (_repo_root() / "analysis_outputs" / "qwen_sft")
    try:
        result = run_experiment(
            train_paths=train,
            eval_path=eval_path,
            out_dir=out_dir,
            holdout_fraction=args.holdout_fraction,
            backend=args.backend,
            model_path=args.model_path,
            max_v3_trajectories=args.max_v3_trajectories,
            epochs=args.epochs,
            use_shipped_splits=bool(args.use_shipped_splits),
            batch_size=args.batch_size,
            grad_accum=args.grad_accum,
            eval_batch_size=args.eval_batch_size,
            strict=bool(args.strict),
            command=args.command,
            model_family=getattr(args, "family", "qwen"),
        )
    except HfUnavailableError:
        return 1
    print(
        json.dumps(
            {
                "sft_actually_ran": result.get("sft_actually_ran"),
                "backend": result.get("backend"),
                "out_dir": str(out_dir),
            },
            indent=2,
        )
    )
    return 0


def _cmd_sft_scaling(args: argparse.Namespace) -> int:
    from safety_monitor.sft.backends import HfUnavailableError
    from safety_monitor.sft.scaling import parse_rungs, run_scaling_ladder

    train = args.train or [_default_train()]
    eval_path = args.eval or _default_eval()
    out_dir = args.out_dir or (
        _repo_root() / "analysis_outputs" / "shieldgemma_sft_scaling"
    )
    try:
        result = run_scaling_ladder(
            train_paths=train,
            eval_path=eval_path,
            out_dir=out_dir,
            backend=args.backend,
            model_path=args.model_path,
            rungs=parse_rungs(args.rungs),
            epochs=args.epochs,
            seed=args.seed,
            smoke=bool(args.smoke),
            batch_size=args.batch_size,
            grad_accum=args.grad_accum,
            eval_batch_size=args.eval_batch_size,
            strict=bool(args.strict),
            command=args.command,
        )
    except HfUnavailableError:
        return 1
    print(
        json.dumps(
            {
                "experiment": result.get("experiment"),
                "sft_actually_ran": result.get("sft_actually_ran"),
                "backend": result.get("backend"),
                "rungs": result.get("rungs"),
                "firewall": result.get("firewall"),
                "out_dir": str(out_dir),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
