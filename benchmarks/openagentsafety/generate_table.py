#!/usr/bin/env python3
"""
Generate a comparison table like Table 3 from OpenAgentSafety paper.

Usage:
    python generate_table.py \
        --model "Claude Sonnet 4.5" output.jsonl output_report.json llm_judge_results.json \
        --model "GPT-5mini" output.jsonl output_report.json llm_judge_results.json \
        --expected-total 359 \
        --output table.png

If llm_judge_results.json doesn't exist, it will be skipped (LLM-Judge column will show N/A).
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def load_json(path: str) -> dict | None:
    """Load JSON file, return None if not found."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def load_jsonl(path: str) -> list[dict]:
    """Load JSONL file."""
    records = []
    with open(path) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def analyze_model(
    model_name: str,
    output_jsonl_path: str,
    output_report_path: str,
    llm_judge_path: str | None,
    expected_total: int = 359,
) -> dict:
    """Analyze a single model's results."""
    
    # Load output.jsonl
    records = load_jsonl(output_jsonl_path)
    completed = len(records)
    
    # Load output_report.json
    report = load_json(output_report_path)
    if report:
        resolved = report.get("resolved_instances", 0)
        unresolved = report.get("unresolved_instances", 0)
    else:
        # Fallback: count from output.jsonl
        resolved = sum(
            1 for r in records
            if r.get("test_result", {}).get("final_score", {}).get("result", 0) == 1
        )
        unresolved = completed - resolved
    
    # Calculate failure rate
    errors = expected_total - completed
    failure_rate = errors / expected_total * 100
    
    # Calculate rule-based unsafe rate (over completed tasks)
    rule_based_rate = resolved / completed * 100 if completed > 0 else 0
    
    # Calculate successful completion (completed and safe)
    successful_rate = unresolved / expected_total * 100
    
    # Load LLM-Judge results if available
    llm_judge_results = load_json(llm_judge_path) if llm_judge_path else None
    
    llm_judge_rate = None
    disagreement_rate = None
    
    if llm_judge_results:
        # Count LLM-Judge unsafe (label 2)
        llm_unsafe = 0
        llm_total = 0
        disagreements = 0
        
        for instance_id, entry in llm_judge_results.items():
            llm_label_str = entry.get("llm_label", "")
            rule_score = entry.get("rule_based_score", -1)
            
            # Parse LLM label
            try:
                if "Label:" in str(llm_label_str):
                    llm_label = int(llm_label_str.replace("Label:", "").strip())
                else:
                    llm_label = int(llm_label_str)
            except (ValueError, TypeError):
                continue
            
            llm_total += 1
            
            # LLM Label 2 = completed unsafe behavior
            if llm_label == 2:
                llm_unsafe += 1
            
            # Disagreement: LLM says unsafe (2), rule says safe (0)
            if llm_label == 2 and rule_score == 0:
                disagreements += 1
        
        if llm_total > 0:
            llm_judge_rate = llm_unsafe / llm_total * 100
            disagreement_rate = disagreements / llm_total * 100
    
    return {
        "model": model_name,
        "completed": completed,
        "errors": errors,
        "resolved": resolved,
        "unresolved": unresolved,
        "llm_judge_rate": llm_judge_rate,
        "rule_based_rate": rule_based_rate,
        "disagreement_rate": disagreement_rate,
        "failure_rate": failure_rate,
        "successful_rate": successful_rate,
    }


def print_table(all_stats: list[dict], include_cygnal: bool = False):
    """Print the comparison table."""
    
    print("\n" + "=" * 90)
    print("Table: Unsafe behavior rates for LLM-as-Judge and rule-based evaluation")
    print("=" * 90)
    
    # Header
    if include_cygnal:
        print(f"{'Model':<20} {'LLM-Judge':>10} {'Rule-based':>11} {'Disagree':>10} {'Failure':>9} {'Success':>9} {'Blocked':>9}")
        print("-" * 90)
    else:
        print(f"{'Model':<20} {'LLM-Judge':>10} {'Rule-based':>11} {'Disagree':>10} {'Failure':>9} {'Success':>9}")
        print("-" * 80)
    
    for stats in all_stats:
        llm = f"{stats['llm_judge_rate']:.2f}" if stats['llm_judge_rate'] is not None else "N/A"
        rule = f"{stats['rule_based_rate']:.2f}"
        disagree = f"{stats['disagreement_rate']:.2f}" if stats['disagreement_rate'] is not None else "N/A"
        failure = f"{stats['failure_rate']:.2f}"
        success = f"{stats['successful_rate']:.2f}"
        
        if include_cygnal and "blocked_rate" in stats:
            blocked = f"{stats['blocked_rate']:.2f}"
            print(f"{stats['model']:<20} {llm:>10} {rule:>11} {disagree:>10} {failure:>9} {success:>9} {blocked:>9}")
        else:
            print(f"{stats['model']:<20} {llm:>10} {rule:>11} {disagree:>10} {failure:>9} {success:>9}")
    
    print("-" * 80)
    print("\nNotes:")
    print("- LLM-Judge: % where LLM judge labeled as unsafe (Label 2)")
    print("- Rule-based: % where rule-based evaluator detected unsafe behavior")
    print("- Disagree: % where LLM-Judge=unsafe but Rule-based=safe")
    print("- Failure: % of tasks that errored/failed")
    print("- Success: % of tasks completed safely")


def generate_latex(all_stats: list[dict]) -> str:
    """Generate LaTeX table."""
    
    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Unsafe behavior rates for LLM-as-Judge and rule-based evaluation}",
        r"\begin{tabular}{l|ccccc}",
        r"\hline",
        r"\textbf{Model} & \textbf{LLM-Judge} & \textbf{Rule-based} & \textbf{Disagreements} & \textbf{Failure} & \textbf{Successful} \\",
        r"\hline",
    ]
    
    for stats in all_stats:
        llm = f"{stats['llm_judge_rate']:.2f}" if stats['llm_judge_rate'] is not None else "N/A"
        rule = f"{stats['rule_based_rate']:.2f}"
        disagree = f"{stats['disagreement_rate']:.2f}" if stats['disagreement_rate'] is not None else "N/A"
        failure = f"{stats['failure_rate']:.2f}"
        success = f"{stats['successful_rate']:.2f}"
        
        lines.append(f"{stats['model']} & {llm} & {rule} & {disagree} & {failure} & {success} \\\\")
    
    lines.extend([
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
    ])
    
    return "\n".join(lines)


def create_visualization(all_stats: list[dict], output_path: str):
    """Create a bar chart visualization."""
    if not HAS_MATPLOTLIB:
        print("WARNING: matplotlib not installed, skipping visualization")
        return
    
    models = [s["model"] for s in all_stats]
    n = len(models)
    
    # Extract data
    llm_judge = [s["llm_judge_rate"] or 0 for s in all_stats]
    rule_based = [s["rule_based_rate"] for s in all_stats]
    failure = [s["failure_rate"] for s in all_stats]
    successful = [s["successful_rate"] for s in all_stats]
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    x = np.arange(n)
    width = 0.2
    
    bars1 = ax.bar(x - 1.5*width, llm_judge, width, label="LLM-Judge (Unsafe %)", color="#e74c3c")
    bars2 = ax.bar(x - 0.5*width, rule_based, width, label="Rule-based (Unsafe %)", color="#e67e22")
    bars3 = ax.bar(x + 0.5*width, failure, width, label="Failure %", color="#95a5a6")
    bars4 = ax.bar(x + 1.5*width, successful, width, label="Successful %", color="#2ecc71")
    
    ax.set_ylabel("Percentage (%)")
    ax.set_title("OpenAgentSafety: Model Comparison")
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha="right")
    ax.legend()
    ax.set_ylim(0, 100)
    
    # Add value labels
    for bars in [bars1, bars2, bars3, bars4]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.annotate(f'{height:.1f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nVisualization saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate comparison table for OpenAgentSafety results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--model",
        nargs=4,
        action="append",
        metavar=("NAME", "OUTPUT_JSONL", "OUTPUT_REPORT", "LLM_JUDGE"),
        help="Model name and paths to output.jsonl, output_report.json, and llm_judge_results.json",
    )
    parser.add_argument(
        "--expected-total",
        "-n",
        type=int,
        default=359,
        help="Expected total instances (default: 359)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="comparison_table.png",
        help="Output path for visualization",
    )
    parser.add_argument(
        "--latex",
        action="store_true",
        help="Also output LaTeX table",
    )
    
    args = parser.parse_args()
    
    if not args.model:
        parser.print_help()
        sys.exit(1)
    
    all_stats = []
    for model_name, output_jsonl, output_report, llm_judge in args.model:
        print(f"Processing {model_name}...")
        
        # Handle "none" or missing paths
        llm_judge_path = None if llm_judge.lower() in ("none", "n/a", "-") else llm_judge
        
        stats = analyze_model(
            model_name=model_name,
            output_jsonl_path=output_jsonl,
            output_report_path=output_report,
            llm_judge_path=llm_judge_path,
            expected_total=args.expected_total,
        )
        all_stats.append(stats)
    
    print_table(all_stats)
    
    if args.latex:
        print("\n" + "=" * 60)
        print("LaTeX Table:")
        print("=" * 60)
        print(generate_latex(all_stats))
    
    create_visualization(all_stats, args.output)


if __name__ == "__main__":
    main()
