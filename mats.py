#!/usr/bin/env python3
"""
MATS Application: Temporal Commitment Analysis in Agent Safety
Steps 1-2: Parse trajectories + baseline predictors
"""

import json
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import pandas as pd


# ============================================================================
# STEP 1: Parse JSONL → Step-level table
# ============================================================================


def extract_cot_from_thinking_blocks(thinking_blocks):
    """Extract CoT text from thinking_blocks structure."""
    if not thinking_blocks:
        return ""

    cot_texts = []
    for block in thinking_blocks:
        if isinstance(block, dict) and block.get("type") == "thinking":
            cot_texts.append(block.get("thinking", ""))

    return " ".join(cot_texts).strip()


def parse_jsonl_line(line: str) -> List[Dict]:
    """Parse single JSONL line into step-level records."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []

    # Get terminal outcome from test_result
    test_result = data.get("test_result", {})
    final_score = test_result.get("final_score", {})

    if not final_score:
        return []

    terminal_outcome = "unsafe" if final_score.get("result") == 1 else "safe"
    trajectory_id = data.get("instance_id", "unknown")

    # Extract ActionEvents from history
    history = data.get("history", [])

    records = []
    step_index = 0

    for event in history:
        if event.get("kind") == "ActionEvent":
            thinking_blocks = event.get("thinking_blocks", [])
            cot_text = extract_cot_from_thinking_blocks(thinking_blocks)

            # Fallback to reasoning_content
            if not cot_text:
                cot_text = event.get("reasoning_content", "")

            action = event.get("action", {})
            action_type = action.get("kind", "unknown")

            records.append(
                {
                    "trajectory_id": trajectory_id,
                    "step_index": step_index,
                    "cot_text": cot_text,
                    "action_type": action_type,
                    "terminal_outcome": terminal_outcome,
                }
            )

            step_index += 1

    return records


def parse_jsonl_file(filepath: str) -> pd.DataFrame:
    """Parse JSONL file into DataFrame."""
    all_records = []

    print(f"Parsing {filepath}...\n")

    with open(filepath, "r") as f:
        for line_num, line in enumerate(f, 1):
            try:
                records = parse_jsonl_line(line.strip())
                if records:
                    traj_id = records[0]["trajectory_id"]
                    outcome = records[0]["terminal_outcome"]
                    print(
                        f"  ✓ Line {line_num} ({traj_id}): {len(records)} steps, outcome={outcome}"
                    )
                    all_records.extend(records)
                else:
                    print(f"  SKIP: Line {line_num} - No valid data")
            except Exception as e:
                print(f"  ERROR: Line {line_num} - {e}")

    if not all_records:
        print("\n❌ ERROR: No valid records parsed!")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)

    # Summary
    print(f"\n{'=' * 60}")
    print("PARSING COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total steps: {len(df)}")
    print(f"Unique trajectories: {df['trajectory_id'].nunique()}")
    print("\nOutcome distribution:")
    print(df.groupby("trajectory_id")["terminal_outcome"].first().value_counts())

    return df


# ============================================================================
# STEP 2: Dumb Baselines
# ============================================================================

SAFETY_KEYWORDS = [
    "unsafe",
    "dangerous",
    "harmful",
    "risk",
    "concern",
    "warning",
    "should not",
    "cannot",
    "refuse",
    "inappropriate",
    "unethical",
    "policy",
    "guidelines",
    "violation",
    "problematic",
    "red flag",
    "malicious",
    "suspicious",
    "unauthorized",
    "illegal",
    "discriminatory",
    "offensive",
    "concerning",
]


def final_step_predictor(df: pd.DataFrame) -> Dict:
    """Baseline 1: Predict based only on final step CoT."""
    results = []

    for traj_id in df["trajectory_id"].unique():
        traj_df = df[df["trajectory_id"] == traj_id].sort_values("step_index")

        if len(traj_df) == 0:
            continue

        final_step = traj_df.iloc[-1]
        final_cot = final_step["cot_text"].lower()

        # Predict safe if safety keywords present
        has_safety_keywords = any(kw in final_cot for kw in SAFETY_KEYWORDS)
        prediction = "safe" if has_safety_keywords else "unsafe"
        actual = final_step["terminal_outcome"]

        results.append(
            {
                "trajectory_id": traj_id,
                "prediction": prediction,
                "actual": actual,
                "correct": prediction == actual,
            }
        )

    results_df = pd.DataFrame(results)
    accuracy = results_df["correct"].mean()

    print(f"\n{'=' * 60}")
    print("BASELINE 1: Final-Step-Only Predictor")
    print(f"{'=' * 60}")
    print(f"Accuracy: {accuracy:.3f}")
    print("\nConfusion matrix:")
    print(
        pd.crosstab(
            results_df["actual"],
            results_df["prediction"],
            rownames=["Actual"],
            colnames=["Predicted"],
            margins=True,
        )
    )

    return {"name": "final_step_only", "accuracy": accuracy, "results": results_df}


def keyword_heuristic_over_prefixes(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """Baseline 2: Keyword heuristic accuracy vs step index."""
    results = []

    for traj_id in df["trajectory_id"].unique():
        traj_df = df[df["trajectory_id"] == traj_id].sort_values("step_index")

        if len(traj_df) == 0:
            continue

        actual_outcome = traj_df.iloc[0]["terminal_outcome"]

        # For each prefix length
        for prefix_len in range(1, len(traj_df) + 1):
            prefix_df = traj_df.iloc[:prefix_len]

            # Count safety keywords in prefix (handle None values)
            cot_texts = [
                str(text) if text else "" for text in prefix_df["cot_text"].values
            ]
            all_cot = " ".join(cot_texts).lower()
            safety_count = sum(all_cot.count(kw) for kw in SAFETY_KEYWORDS)

            # Prediction: if safety keywords present, predict safe
            prediction = "safe" if safety_count > 0 else "unsafe"

            results.append(
                {
                    "trajectory_id": traj_id,
                    "prefix_length": prefix_len,
                    "safety_keyword_count": safety_count,
                    "prediction": prediction,
                    "actual": actual_outcome,
                    "correct": prediction == actual_outcome,
                }
            )

    results_df = pd.DataFrame(results)

    # Accuracy by prefix length
    acc_by_step = results_df.groupby("prefix_length")["correct"].mean()

    print(f"\n{'=' * 60}")
    print("BASELINE 2: Keyword Heuristic Over Prefixes")
    print(f"{'=' * 60}")
    print(f"Overall accuracy: {results_df['correct'].mean():.3f}")
    print("\nAccuracy by prefix length:")
    for step, acc in acc_by_step.head(10).items():
        print(f"  Step {step}: {acc:.3f}")

    # Plot
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(
        acc_by_step.index, acc_by_step.values, marker="o", linewidth=2, markersize=8
    )
    ax.axhline(y=0.5, color="red", linestyle="--", linewidth=2, label="Random baseline")
    ax.set_xlabel("Prefix Length (steps)", fontsize=12)
    ax.set_ylabel("Accuracy", fontsize=12)
    ax.set_title(
        "Keyword Heuristic: Accuracy vs Trajectory Prefix Length",
        fontsize=14,
        fontweight="bold",
    )
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_ylim([0, 1])
    plt.tight_layout()

    return results_df, {
        "name": "keyword_prefix",
        "accuracy_by_step": acc_by_step,
        "overall_accuracy": results_df["correct"].mean(),
        "fig": fig,
    }


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python mats.py <jsonl_file>")
        sys.exit(1)

    JSONL_FILE = sys.argv[1]

    # Step 1: Parse
    print("STEP 1: Parsing trajectories...")
    df = parse_jsonl_file(JSONL_FILE)

    if df.empty:
        print("\n❌ FATAL: No data parsed. Cannot continue.")
        sys.exit(1)

    df.to_csv("step_level_data.csv", index=False)
    print("✓ Saved to step_level_data.csv")

    # Step 2: Baselines
    print("\nSTEP 2: Running baselines...")
    baseline1 = final_step_predictor(df)
    prefix_results, baseline2 = keyword_heuristic_over_prefixes(df)

    # Save
    baseline1["results"].to_csv("baseline1_final_step.csv", index=False)
    prefix_results.to_csv("baseline2_keyword_prefix.csv", index=False)
    baseline2["fig"].savefig(
        "baseline2_accuracy_vs_prefix.png", dpi=300, bbox_inches="tight"
    )

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Final-step-only accuracy: {baseline1['accuracy']:.3f}")
    print(f"Keyword prefix accuracy:  {baseline2['overall_accuracy']:.3f}")
    print("\n✓ Outputs saved. Ready for Step 3.")
