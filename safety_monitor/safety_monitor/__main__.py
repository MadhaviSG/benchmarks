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

    args = parser.parse_args(argv)
    return args.func(args)


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
