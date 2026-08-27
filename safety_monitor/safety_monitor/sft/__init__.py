"""Local Qwen SFT for the external per-action safety critic.

Train on synthetic v4+v5 contrastive pairs only. Evaluate on real v3-mined
trajectories, with a held-out synthetic task slice as a leakage diagnostic.
"""

from safety_monitor.sft.data import (
    SFTExample,
    build_examples,
    split_by_shipped_fields,
    split_synthetic_tasks,
)
from safety_monitor.sft.experiment import run_experiment
from safety_monitor.sft.metrics import action_metrics, trajectory_metrics
from safety_monitor.sft.scaling import nested_stratified_subsets, run_scaling_ladder


__all__ = [
    "SFTExample",
    "action_metrics",
    "build_examples",
    "nested_stratified_subsets",
    "run_experiment",
    "run_scaling_ladder",
    "split_by_shipped_fields",
    "split_synthetic_tasks",
    "trajectory_metrics",
]


__all__ = [
    "SFTExample",
    "action_metrics",
    "build_examples",
    "run_experiment",
    "split_synthetic_tasks",
    "trajectory_metrics",
]
