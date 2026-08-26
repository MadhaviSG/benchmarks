#!/usr/bin/env python3
"""Plot harm-outcome category distribution for the synthetic OAS corpus (v4+v5+v6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from compare_og_mg_v2 import (  # noqa: E402
    MG_V4,
    MG_V5,
    MG_V6,
    OUTCOME_CATEGORIES,
    load_jsonl,
    load_mg_outcome_map,
    outcome_distribution,
)


OUTPUT_DIR = SCRIPT_DIR
IMAGE_PATH = OUTPUT_DIR / "synthetic_outcome_distribution.png"
JSON_PATH = OUTPUT_DIR / "synthetic_outcome_distribution.json"
MD_PATH = OUTPUT_DIR / "synthetic_outcome_distribution.md"


def main() -> None:
    v4_rows = load_jsonl(MG_V4)
    v5_rows = load_jsonl(MG_V5)
    v6_rows = load_jsonl(MG_V6)
    mg_rows = v4_rows + v5_rows + v6_rows

    outcome_map = load_mg_outcome_map()
    combined = outcome_distribution(mg_rows, use_seed=True, outcome_map=outcome_map)
    by_version = {
        "v4": dict(
            outcome_distribution(v4_rows, use_seed=True, outcome_map=outcome_map)
        ),
        "v5": dict(
            outcome_distribution(v5_rows, use_seed=True, outcome_map=outcome_map)
        ),
        "v6": dict(
            outcome_distribution(v6_rows, use_seed=True, outcome_map=outcome_map)
        ),
    }

    counts = {cat: combined.get(cat, 0) for cat in OUTCOME_CATEGORIES}
    total = sum(counts.values())

    # Bar chart — sort by count descending for readability.
    sorted_items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    labels = [item[0] for item in sorted_items]
    values = [item[1] for item in sorted_items]

    fig, ax = plt.subplots(figsize=(11, 6))
    bars = ax.bar(
        range(len(labels)), values, color="#4C72B0", edgecolor="white", linewidth=0.6
    )
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=10)
    ax.set_ylabel("Number of tasks")
    ax.set_title(
        f"Synthetic OAS corpus: harm outcome distribution\n"
        f"(v4 + v5 + v6, n = {total} tasks)",
        fontsize=13,
        pad=12,
    )
    ax.set_ylim(0, max(values) * 1.12)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)

    for bar, value in zip(bars, values, strict=True):
        pct = 100.0 * value / total if total else 0.0
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1.5,
            f"{value}\n({pct:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    fig.tight_layout()
    fig.savefig(IMAGE_PATH, dpi=150, bbox_inches="tight")
    plt.close(fig)

    metadata = {
        "corpus": "synthetic OAS (mg combined)",
        "total_tasks": total,
        "sources": {
            "v4": {"path": str(MG_V4), "n_tasks": len(v4_rows)},
            "v5": {"path": str(MG_V5), "n_tasks": len(v5_rows)},
            "v6": {"path": str(MG_V6), "n_tasks": len(v6_rows)},
        },
        "outcome_assignment": (
            "Primary: outcome_category from v5 seed_coverage.json and "
            "v6 accepted_seeds.jsonl. Fallback for v4 (no seed metadata): "
            "regex rubric on checkpoint text (compare_og_mg_v2.classify_og_outcome)."
        ),
        "counts_by_category": counts,
        "percent_by_category": {
            cat: round(100.0 * counts[cat] / total, 2) if total else 0.0
            for cat in OUTCOME_CATEGORIES
        },
        "counts_by_version": by_version,
    }
    JSON_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# Synthetic OAS outcome distribution",
        "",
        f"**Corpus:** v4 + v5 + v6 train JSONL ({total} tasks)",
        "",
        "| Outcome category | Count | % |",
        "|---|---:|---:|",
    ]
    for cat in OUTCOME_CATEGORIES:
        n = counts[cat]
        pct = 100.0 * n / total if total else 0.0
        md_lines.append(f"| {cat} | {n} | {pct:.1f}% |")
    md_lines.extend(
        [
            "",
            "## Data sources",
            "",
            f"- v4: `{MG_V4}` ({len(v4_rows)} tasks)",
            f"- v5: `{MG_V5}` ({len(v5_rows)} tasks)",
            f"- v6: `{MG_V6}` ({len(v6_rows)} tasks)",
            "",
            "## Outcome assignment",
            "",
            metadata["outcome_assignment"],
            "",
            f"![Outcome distribution]({IMAGE_PATH.name})",
            "",
        ]
    )
    MD_PATH.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"Saved chart: {IMAGE_PATH}")
    print(f"Saved counts: {JSON_PATH}")
    print(f"Saved summary: {MD_PATH}")
    print(f"Total tasks: {total}")
    for cat in OUTCOME_CATEGORIES:
        print(f"  {cat}: {counts[cat]}")


if __name__ == "__main__":
    main()
