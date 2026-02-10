#!/usr/bin/env python3
"""
Step 4: Model Biology - Matched-Prefix Analysis
Compare reasoning patterns at fixed steps between safe vs unsafe trajectories
"""


import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


# ============================================================================
# Analysis Functions
# ============================================================================


def extract_reasoning_features(cot_text):
    """Extract features from CoT text."""
    if not cot_text or pd.isna(cot_text):
        cot_text = ""

    text_lower = cot_text.lower()

    return {
        "length": len(cot_text),
        "word_count": len(cot_text.split()),
        # Safety awareness
        "safety_mention": int(
            any(
                kw in text_lower
                for kw in [
                    "unsafe",
                    "dangerous",
                    "harmful",
                    "risk",
                    "concern",
                    "warning",
                ]
            )
        ),
        "policy_mention": int(
            any(
                kw in text_lower
                for kw in ["policy", "guidelines", "rules", "should not", "cannot"]
            )
        ),
        "ethical_mention": int(
            any(
                kw in text_lower
                for kw in ["unethical", "inappropriate", "wrong", "problematic"]
            )
        ),
        # Task execution focus
        "task_focus": int(
            any(
                kw in text_lower
                for kw in ["task", "complete", "execute", "implement", "proceed"]
            )
        ),
        "file_operations": int(
            any(
                kw in text_lower
                for kw in ["file", "read", "write", "save", "create", "delete"]
            )
        ),
        # Deliberation markers
        "questioning": int(
            any(
                kw in text_lower
                for kw in ["?", "should i", "need to check", "verify", "confirm"]
            )
        ),
        "hedging": int(
            any(
                kw in text_lower
                for kw in ["might", "could", "perhaps", "maybe", "possibly"]
            )
        ),
        "certainty": int(
            any(
                kw in text_lower
                for kw in ["will", "must", "definitely", "clearly", "obviously"]
            )
        ),
        # NPC interaction
        "npc_mention": int(
            any(
                kw in text_lower for kw in ["npc", "user", "they", "requested", "asked"]
            )
        ),
    }


def matched_prefix_analysis(df, target_steps=[5, 10, 15, 20]):
    """
    Compare safe vs unsafe trajectories at matched prefix lengths.
    Only analyze trajectories that reach each target step.
    """

    results = []

    for target_step in target_steps:
        # Get trajectories that have at least target_step
        traj_max_steps = df.groupby("trajectory_id")["step_index"].max()
        valid_trajs = traj_max_steps[traj_max_steps >= target_step].index

        for traj_id in valid_trajs:
            traj_df = df[df["trajectory_id"] == traj_id].sort_values("step_index")

            # Get the step at target_step index
            step_row = traj_df[traj_df["step_index"] == target_step]
            if step_row.empty:
                continue

            step_row = step_row.iloc[0]
            outcome = step_row["terminal_outcome"]

            # Extract features from CoT at this step
            features = extract_reasoning_features(step_row["cot_text"])
            features["trajectory_id"] = traj_id
            features["step"] = target_step
            features["outcome"] = outcome

            results.append(features)

    return pd.DataFrame(results)


def compute_feature_differences(features_df, target_steps):
    """Compute mean differences between safe and unsafe at each step."""

    differences = []

    for step in target_steps:
        step_df = features_df[features_df["step"] == step]

        if len(step_df) < 2:
            continue

        safe_df = step_df[step_df["outcome"] == "safe"]
        unsafe_df = step_df[step_df["outcome"] == "unsafe"]

        if len(safe_df) == 0 or len(unsafe_df) == 0:
            continue

        feature_cols = [
            col
            for col in step_df.columns
            if col not in ["trajectory_id", "step", "outcome"]
        ]

        for feature in feature_cols:
            safe_mean = safe_df[feature].mean()
            unsafe_mean = unsafe_df[feature].mean()
            diff = unsafe_mean - safe_mean

            differences.append(
                {
                    "step": step,
                    "feature": feature,
                    "safe_mean": safe_mean,
                    "unsafe_mean": unsafe_mean,
                    "difference": diff,
                    "n_safe": len(safe_df),
                    "n_unsafe": len(unsafe_df),
                }
            )

    return pd.DataFrame(differences)


# ============================================================================
# Visualization
# ============================================================================


def plot_feature_heatmap(diff_df, target_steps):
    """Plot heatmap of feature differences across steps."""

    # Pivot for heatmap
    pivot = diff_df.pivot(index="feature", columns="step", values="difference")
    pivot = pivot.reindex(target_steps, axis=1)

    # Sort by mean absolute difference
    pivot["mean_abs"] = pivot.abs().mean(axis=1)
    pivot = pivot.sort_values("mean_abs", ascending=False).drop("mean_abs", axis=1)

    # Plot
    fig, ax = plt.subplots(figsize=(10, 8))

    sns.heatmap(
        pivot,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        center=0,
        cbar_kws={"label": "Difference (Unsafe - Safe)"},
        linewidths=0.5,
        ax=ax,
    )

    ax.set_xlabel("Step", fontsize=12, fontweight="bold")
    ax.set_ylabel("Feature", fontsize=12, fontweight="bold")
    ax.set_title(
        "Reasoning Feature Differences: Unsafe vs Safe Trajectories\n(at matched prefix lengths)",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )

    plt.tight_layout()
    return fig


def plot_feature_trajectories(features_df, top_features=6):
    """Plot how top features evolve over steps for safe vs unsafe."""

    # Calculate overall differences to find top features
    feature_cols = [
        col
        for col in features_df.columns
        if col not in ["trajectory_id", "step", "outcome"]
    ]

    overall_diffs = {}
    for feature in feature_cols:
        safe_mean = features_df[features_df["outcome"] == "safe"][feature].mean()
        unsafe_mean = features_df[features_df["outcome"] == "unsafe"][feature].mean()
        overall_diffs[feature] = abs(unsafe_mean - safe_mean)

    top_features_list = sorted(overall_diffs.items(), key=lambda x: x[1], reverse=True)[
        :top_features
    ]
    top_features_list = [f[0] for f in top_features_list]

    # Plot
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    axes = axes.flatten()

    for idx, feature in enumerate(top_features_list):
        ax = axes[idx]

        for outcome, color, marker in [
            ("safe", "#2ca02c", "o"),
            ("unsafe", "#d62728", "s"),
        ]:
            outcome_df = features_df[features_df["outcome"] == outcome]
            grouped = outcome_df.groupby("step")[feature].agg(["mean", "sem"])

            ax.errorbar(
                grouped.index,
                grouped["mean"],
                yerr=grouped["sem"],
                marker=marker,
                linewidth=2,
                markersize=8,
                capsize=5,
                label=outcome.capitalize(),
                color=color,
                alpha=0.8,
            )

        ax.set_xlabel("Step", fontsize=10, fontweight="bold")
        ax.set_ylabel("Mean Value", fontsize=10, fontweight="bold")
        ax.set_title(feature.replace("_", " ").title(), fontsize=11, fontweight="bold")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

    plt.suptitle(
        "Top Features: Safe vs Unsafe Trajectory Evolution",
        fontsize=14,
        fontweight="bold",
        y=1.00,
    )
    plt.tight_layout()
    return fig


# ============================================================================
# Main Analysis
# ============================================================================

if __name__ == "__main__":
    # Load data
    df = pd.read_csv("step_level_data.csv")

    print(f"\n{'=' * 60}")
    print("STEP 4: Model Biology - Matched-Prefix Analysis")
    print(f"{'=' * 60}")
    print("Question: Do reasoning patterns differ between safe/unsafe")
    print("          trajectories at matched prefix lengths?")

    # Analysis at key steps
    target_steps = [5, 10, 15, 20]

    print(f"\nAnalyzing steps: {target_steps}")
    features_df = matched_prefix_analysis(df, target_steps)

    # Sample sizes
    print("\nSample sizes at each step:")
    for step in target_steps:
        step_df = features_df[features_df["step"] == step]
        n_safe = len(step_df[step_df["outcome"] == "safe"])
        n_unsafe = len(step_df[step_df["outcome"] == "unsafe"])
        print(f"  Step {step:2d}: safe={n_safe:2d}, unsafe={n_unsafe:2d}")

    # Compute differences
    diff_df = compute_feature_differences(features_df, target_steps)

    # Show top differences
    print(f"\n{'=' * 60}")
    print("Top Feature Differences (averaged across steps):")
    print(f"{'=' * 60}")

    avg_diffs = diff_df.groupby("feature")["difference"].agg(["mean", "std"]).abs()
    avg_diffs = avg_diffs.sort_values("mean", ascending=False)

    for feature, row in avg_diffs.head(10).iterrows():
        print(f"  {feature:20s}: Δ = {row['mean']:+.3f} ± {row['std']:.3f}")

    # Visualizations
    print("\nGenerating visualizations...")

    fig1 = plot_feature_heatmap(diff_df, target_steps)
    fig1.savefig("step4_feature_heatmap.png", dpi=300, bbox_inches="tight")

    fig2 = plot_feature_trajectories(features_df)
    fig2.savefig("step4_feature_trajectories.png", dpi=300, bbox_inches="tight")

    # Save data
    features_df.to_csv("step4_matched_features.csv", index=False)
    diff_df.to_csv("step4_feature_differences.csv", index=False)

    print(f"\n{'=' * 60}")
    print("INTERPRETATION GUIDANCE")
    print(f"{'=' * 60}")
    print("Look for:")
    print("  - Consistent differences across multiple steps")
    print("  - Features that diverge over time")
    print("  - Small effect sizes = weak behavioral signal")
    print("\n✓ Results saved:")
    print("  - step4_feature_heatmap.png")
    print("  - step4_feature_trajectories.png")
    print("  - step4_matched_features.csv")
    print("  - step4_feature_differences.csv")
