"""CLI entrypoint for safety_monitor synthesis commands."""

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
