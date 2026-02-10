#!/usr/bin/env python3
"""
Step 3: Temporal Commitment Test
Analyze: Do unsafe outcomes become inevitable at certain prefix lengths?
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def temporal_commitment_analysis(df: pd.DataFrame):
    """
    Compute P(unsafe | prefix length = k) for each step k.
    Shows if unsafe outcomes become predictable early in trajectories.
    """

    # For each trajectory and prefix length, what's the actual outcome?
    results = []

    for traj_id in df["trajectory_id"].unique():
        traj_df = df[df["trajectory_id"] == traj_id].sort_values("step_index")
        actual_outcome = traj_df.iloc[0]["terminal_outcome"]
        max_steps = len(traj_df)

        for prefix_len in range(1, max_steps + 1):
            results.append(
                {
                    "trajectory_id": traj_id,
                    "prefix_length": prefix_len,
                    "actual_outcome": actual_outcome,
                    "is_unsafe": 1 if actual_outcome == "unsafe" else 0,
                }
            )

    results_df = pd.DataFrame(results)

    # Compute P(unsafe | prefix_length = k)
    prob_unsafe_by_step = (
        results_df.groupby("prefix_length")["is_unsafe"]
        .agg([("p_unsafe", "mean"), ("n_trajectories", "count")])
        .reset_index()
    )

    # Compute 95% confidence intervals (Wilson score)
    def wilson_ci(p, n, z=1.96):
        """Wilson score confidence interval"""
        denominator = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denominator
        margin = z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denominator
        return center - margin, center + margin

    prob_unsafe_by_step["ci_lower"] = prob_unsafe_by_step.apply(
        lambda row: wilson_ci(row["p_unsafe"], row["n_trajectories"])[0], axis=1
    )
    prob_unsafe_by_step["ci_upper"] = prob_unsafe_by_step.apply(
        lambda row: wilson_ci(row["p_unsafe"], row["n_trajectories"])[1], axis=1
    )

    # Print results
    print(f"\n{'=' * 60}")
    print("STEP 3: Temporal Commitment Analysis")
    print(f"{'=' * 60}")
    print("Question: Does P(unsafe | prefix length) change over trajectory?")
    print(f"\nBase rate: P(unsafe) = {results_df['is_unsafe'].mean():.3f}")
    print("\nP(unsafe | prefix length = k):")
    for _, row in prob_unsafe_by_step.head(15).iterrows():
        print(
            f"  Step {int(row['prefix_length']):2d}: {row['p_unsafe']:.3f} "
            f"(n={int(row['n_trajectories']):2d}, CI=[{row['ci_lower']:.3f}, {row['ci_upper']:.3f}])"
        )

    # Plot
    fig, ax = plt.subplots(figsize=(12, 7))

    # Main line
    ax.plot(
        prob_unsafe_by_step["prefix_length"],
        prob_unsafe_by_step["p_unsafe"],
        marker="o",
        linewidth=2.5,
        markersize=8,
        color="#d62728",
        label="P(unsafe | prefix length)",
    )

    # Confidence interval
    ax.fill_between(
        prob_unsafe_by_step["prefix_length"],
        prob_unsafe_by_step["ci_lower"],
        prob_unsafe_by_step["ci_upper"],
        alpha=0.2,
        color="#d62728",
    )

    # Base rate line
    base_rate = results_df["is_unsafe"].mean()
    ax.axhline(
        y=base_rate,
        color="black",
        linestyle="--",
        linewidth=2,
        label=f"Base rate P(unsafe) = {base_rate:.3f}",
    )

    # Sample size annotation
    ax2 = ax.twinx()
    ax2.bar(
        prob_unsafe_by_step["prefix_length"],
        prob_unsafe_by_step["n_trajectories"],
        alpha=0.15,
        color="gray",
        width=0.8,
    )
    ax2.set_ylabel("Number of trajectories", fontsize=11, color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")

    # Formatting
    ax.set_xlabel("Prefix Length (steps)", fontsize=13, fontweight="bold")
    ax.set_ylabel("P(unsafe | prefix)", fontsize=13, fontweight="bold")
    ax.set_title(
        "Temporal Commitment: Does Unsafe Outcome Become Inevitable?",
        fontsize=15,
        fontweight="bold",
        pad=20,
    )
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(True, alpha=0.3, axis="y")
    ax.set_ylim([0, 1])

    plt.tight_layout()

    return prob_unsafe_by_step, fig


if __name__ == "__main__":
    # Load data from Step 1-2
    df = pd.read_csv("step_level_data.csv")

    print(f"Loaded {len(df)} steps from {df['trajectory_id'].nunique()} trajectories")

    # Run temporal commitment analysis
    prob_df, fig = temporal_commitment_analysis(df)

    # Save results
    prob_df.to_csv("step3_temporal_commitment.csv", index=False)
    fig.savefig("step3_temporal_commitment.png", dpi=300, bbox_inches="tight")

    print(f"\n{'=' * 60}")
    print("INTERPRETATION")
    print(f"{'=' * 60}")
    print("If P(unsafe | prefix) stays flat → outcome NOT predictable early")
    print("If P(unsafe | prefix) rises/falls → commitment happens at specific steps")
    print("\n✓ Results saved:")
    print("  - step3_temporal_commitment.csv")
    print("  - step3_temporal_commitment.png")
