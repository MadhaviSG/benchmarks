#!/usr/bin/env python3
"""
Analyze and visualize OpenAgentSafety benchmark results across multiple models.

Usage:
    python analyze_results.py \
        --model "Claude Sonnet" path/to/claude/output.jsonl \
        --model "GPT-4o" path/to/gpt4o/output.jsonl \
        --model "DeepSeek" path/to/deepseek/output.jsonl \
        --model "Llama" path/to/llama/output.jsonl \
        --output results_analysis.png

Or for a single model summary:
    python analyze_results.py path/to/output.jsonl --summary
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Optional imports for visualization
try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    import seaborn as sns
    HAS_SEABORN = True
except ImportError:
    HAS_SEABORN = False


def load_results(jsonl_path: str) -> list[dict]:
    """Load results from an output.jsonl file."""
    records = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def analyze_model(
    records: list[dict], model_name: str = "Model", expected_total: int = 359
) -> dict:
    """Analyze results for a single model."""
    completed = len(records)
    # Errors are instances that didn't make it to output.jsonl
    errors = expected_total - completed

    stats = {
        "model": model_name,
        "total": completed,  # Completed evaluations
        "expected_total": expected_total,
        "blocked": 0,
        "not_blocked": 0,
        "errors": errors,  # Missing from output.jsonl
        "agent_risk_when_blocked": Counter(),
        "blocked_tools": Counter(),
        "instance_results": {},
    }

    for r in records:
        instance_id = r.get("instance_id", "unknown")
        test_result = r.get("test_result", {})

        security_blocked = test_result.get("security_blocked", False)
        blocked_actions = test_result.get("blocked_actions", [])

        if security_blocked:
            stats["blocked"] += 1
            stats["instance_results"][instance_id] = "blocked"

            # Analyze blocked actions
            for action in blocked_actions:
                agent_risk = action.get("agent_risk", "UNKNOWN")
                tool_name = action.get("tool_name", "unknown")
                stats["agent_risk_when_blocked"][agent_risk] += 1
                stats["blocked_tools"][tool_name] += 1
        else:
            stats["not_blocked"] += 1
            stats["instance_results"][instance_id] = "not_blocked"

    # Calculate percentages based on completed evaluations
    if completed > 0:
        stats["block_rate"] = stats["blocked"] / completed * 100
        stats["pass_through_rate"] = stats["not_blocked"] / completed * 100
    else:
        stats["block_rate"] = 0
        stats["pass_through_rate"] = 0

    stats["error_rate"] = errors / expected_total * 100 if expected_total > 0 else 0

    return stats


def print_summary(stats: dict):
    """Print a text summary of the analysis."""
    print(f"\n{'=' * 60}")
    print(f"Model: {stats['model']}")
    print(f"{'=' * 60}")
    print(f"Expected instances:  {stats['expected_total']}")
    print(f"Completed:           {stats['total']}")
    print(f"Blocked by Cygnal:   {stats['blocked']} ({stats['block_rate']:.1f}%)")
    print(f"Not blocked:         {stats['not_blocked']} ({stats['pass_through_rate']:.1f}%)")
    print(f"Errors/Missing:      {stats['errors']} ({stats['error_rate']:.1f}%)")
    print()

    if stats["agent_risk_when_blocked"]:
        print("Agent's risk assessment when blocked:")
        for risk, count in sorted(stats["agent_risk_when_blocked"].items()):
            pct = count / stats["blocked"] * 100 if stats["blocked"] > 0 else 0
            print(f"  {risk:10s}: {count:4d} ({pct:.1f}%)")
        print()

    if stats["blocked_tools"]:
        print("Tools blocked:")
        for tool, count in stats["blocked_tools"].most_common(5):
            print(f"  {tool}: {count}")


def print_comparison_table(all_stats: list[dict]):
    """Print a comparison table across models."""
    print("\n" + "=" * 80)
    print("COMPARISON ACROSS MODELS")
    print("=" * 80)

    # Header
    print(f"{'Model':<20} {'Total':>8} {'Blocked':>10} {'Not Blocked':>12} {'Errors':>8} {'Block %':>10}")
    print("-" * 80)

    for stats in all_stats:
        print(
            f"{stats['model']:<20} "
            f"{stats['total']:>8} "
            f"{stats['blocked']:>10} "
            f"{stats['not_blocked']:>12} "
            f"{stats['errors']:>8} "
            f"{stats['block_rate']:>9.1f}%"
        )

    print("-" * 80)

    # Agent risk mismatch analysis
    print("\nAgent Risk Assessment When Blocked (Cygnal said HIGH, agent said...):")
    print(f"{'Model':<20} {'LOW':>10} {'MEDIUM':>10} {'HIGH':>10} {'UNKNOWN':>10}")
    print("-" * 80)

    for stats in all_stats:
        risks = stats["agent_risk_when_blocked"]
        print(
            f"{stats['model']:<20} "
            f"{risks.get('LOW', 0):>10} "
            f"{risks.get('MEDIUM', 0):>10} "
            f"{risks.get('HIGH', 0):>10} "
            f"{risks.get('UNKNOWN', 0):>10}"
        )


def create_visualizations(all_stats: list[dict], output_path: str):
    """Create visualization charts."""
    if not HAS_MATPLOTLIB:
        print("WARNING: matplotlib not installed. Skipping visualizations.")
        print("Install with: pip install matplotlib seaborn")
        return

    if HAS_SEABORN:
        sns.set_theme(style="whitegrid")

    models = [s["model"] for s in all_stats]
    n_models = len(models)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("OpenAgentSafety - Cygnal Blocking Analysis", fontsize=14, fontweight="bold")

    # 1. Stacked bar chart: Blocked vs Not Blocked vs Errors
    ax1 = axes[0, 0]
    blocked = [s["blocked"] for s in all_stats]
    not_blocked = [s["not_blocked"] for s in all_stats]
    errors = [s["errors"] for s in all_stats]
    expected_total = all_stats[0]["expected_total"] if all_stats else 359

    x = np.arange(n_models)
    width = 0.6

    bars_blocked = ax1.bar(x, blocked, width, label="Blocked", color="#e74c3c")
    bars_not_blocked = ax1.bar(
        x, not_blocked, width, bottom=blocked, label="Not Blocked", color="#2ecc71"
    )
    bars_errors = ax1.bar(
        x,
        errors,
        width,
        bottom=[b + n for b, n in zip(blocked, not_blocked)],
        label="Errors",
        color="#95a5a6",
    )

    # Add error count labels on gray bars
    for i, (bar, err_count) in enumerate(zip(bars_errors, errors)):
        if err_count > 0:
            # Position label in the middle of the gray bar
            bar_bottom = blocked[i] + not_blocked[i]
            bar_center = bar_bottom + err_count / 2
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                bar_center,
                str(err_count),
                ha="center",
                va="center",
                fontsize=10,
                fontweight="bold",
                color="white",
            )

    ax1.set_ylabel("Number of Instances")
    ax1.set_title(f"Evaluation Outcomes by Model (n={expected_total})")
    ax1.set_xticks(x)
    ax1.set_xticklabels(models, rotation=15, ha="right")
    ax1.legend(loc="upper right")

    # 2. Block rate comparison
    ax2 = axes[0, 1]
    block_rates = [s["block_rate"] for s in all_stats]
    colors = plt.cm.RdYlGn_r(np.array(block_rates) / 100)  # Red = high block rate

    bars = ax2.bar(x, block_rates, width, color=colors)
    ax2.set_ylabel("Block Rate (%)")
    ax2.set_title("Cygnal Block Rate by Model")
    ax2.set_xticks(x)
    ax2.set_xticklabels(models, rotation=15, ha="right")
    ax2.set_ylim(0, 100)

    # Add value labels
    for bar, rate in zip(bars, block_rates):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            f"{rate:.1f}%",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    # 3. Agent risk assessment heatmap when blocked
    ax3 = axes[1, 0]
    risk_levels = ["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    heatmap_data = []

    for stats in all_stats:
        row = [stats["agent_risk_when_blocked"].get(r, 0) for r in risk_levels]
        heatmap_data.append(row)

    heatmap_data = np.array(heatmap_data)

    if HAS_SEABORN:
        sns.heatmap(
            heatmap_data,
            annot=True,
            fmt="d",
            cmap="YlOrRd",
            xticklabels=risk_levels,
            yticklabels=models,
            ax=ax3,
        )
    else:
        im = ax3.imshow(heatmap_data, cmap="YlOrRd", aspect="auto")
        ax3.set_xticks(range(len(risk_levels)))
        ax3.set_xticklabels(risk_levels)
        ax3.set_yticks(range(len(models)))
        ax3.set_yticklabels(models)
        # Add annotations
        for i in range(len(models)):
            for j in range(len(risk_levels)):
                ax3.text(j, i, str(heatmap_data[i, j]), ha="center", va="center")
        plt.colorbar(im, ax=ax3)

    ax3.set_title("Agent's Risk Assessment When Blocked\n(Cygnal assessed HIGH)")
    ax3.set_xlabel("Agent's Predicted Risk")

    # 4. Risk mismatch analysis (agent said LOW but Cygnal blocked)
    ax4 = axes[1, 1]
    low_when_blocked = [s["agent_risk_when_blocked"].get("LOW", 0) for s in all_stats]
    medium_when_blocked = [s["agent_risk_when_blocked"].get("MEDIUM", 0) for s in all_stats]
    high_when_blocked = [s["agent_risk_when_blocked"].get("HIGH", 0) for s in all_stats]

    width = 0.25
    x = np.arange(n_models)

    ax4.bar(x - width, low_when_blocked, width, label="Agent: LOW", color="#3498db")
    ax4.bar(x, medium_when_blocked, width, label="Agent: MEDIUM", color="#f39c12")
    ax4.bar(x + width, high_when_blocked, width, label="Agent: HIGH", color="#e74c3c")

    ax4.set_ylabel("Count")
    ax4.set_title("Agent Risk Prediction vs Cygnal Block\n(Lower = More False Negatives by Agent)")
    ax4.set_xticks(x)
    ax4.set_xticklabels(models, rotation=15, ha="right")
    ax4.legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nVisualization saved to: {output_path}")


def export_csv(all_stats: list[dict], output_path: str):
    """Export results to CSV."""
    csv_path = output_path.replace(".png", ".csv")
    with open(csv_path, "w") as f:
        # Header
        f.write("Model,Expected,Completed,Blocked,Not Blocked,Errors,Block Rate %,Error Rate %,")
        f.write("Agent LOW when blocked,Agent MEDIUM when blocked,Agent HIGH when blocked\n")

        for stats in all_stats:
            risks = stats["agent_risk_when_blocked"]
            f.write(
                f"{stats['model']},{stats['expected_total']},{stats['total']},"
                f"{stats['blocked']},{stats['not_blocked']},{stats['errors']},"
                f"{stats['block_rate']:.1f},{stats['error_rate']:.1f},"
                f"{risks.get('LOW', 0)},{risks.get('MEDIUM', 0)},{risks.get('HIGH', 0)}\n"
            )

    print(f"CSV exported to: {csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze OpenAgentSafety benchmark results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single model summary
  python analyze_results.py output.jsonl --summary

  # Compare multiple models with visualization
  python analyze_results.py \\
      --model "Claude Sonnet" results/claude/output.jsonl \\
      --model "GPT-4o" results/gpt4o/output.jsonl \\
      --output comparison.png
        """,
    )

    parser.add_argument(
        "jsonl_path",
        nargs="?",
        help="Path to output.jsonl (for single model summary)",
    )
    parser.add_argument(
        "--model",
        nargs=2,
        action="append",
        metavar=("NAME", "PATH"),
        help="Model name and path to its output.jsonl (can be repeated)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="analysis.png",
        help="Output path for visualization (default: analysis.png)",
    )
    parser.add_argument(
        "--summary",
        "-s",
        action="store_true",
        help="Print summary only (no visualization)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also export results to CSV",
    )
    parser.add_argument(
        "--expected-total",
        "-n",
        type=int,
        default=359,
        help="Expected total number of instances (default: 359)",
    )

    args = parser.parse_args()

    # Single model mode
    if args.jsonl_path and not args.model:
        records = load_results(args.jsonl_path)
        stats = analyze_model(
            records, Path(args.jsonl_path).parent.name, expected_total=args.expected_total
        )
        print_summary(stats)
        return

    # Multi-model comparison
    if not args.model:
        parser.print_help()
        sys.exit(1)

    all_stats = []
    for model_name, jsonl_path in args.model:
        print(f"Loading {model_name} from {jsonl_path}...")
        records = load_results(jsonl_path)
        stats = analyze_model(records, model_name, expected_total=args.expected_total)
        all_stats.append(stats)
        print_summary(stats)

    print_comparison_table(all_stats)

    if args.csv:
        export_csv(all_stats, args.output)

    if not args.summary:
        create_visualizations(all_stats, args.output)


if __name__ == "__main__":
    main()
