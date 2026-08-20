"""Stratified train/dev/test splits for the v6 corpus."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from safety_monitor.synthesis.v5_types import V5Seed


SPLIT_SALT = "v6-synthetic-500"
TARGET_TRAIN = 350
TARGET_DEV = 75
TARGET_TEST = 75


def _bucket(instance_id: str, salt: str) -> float:
    digest = hashlib.sha1(f"{salt}:{instance_id}".encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def strata_key(seed: V5Seed) -> str:
    svc = seed.services[0] if seed.services else "filesystem-only"
    return f"{seed.outcome_category}|{seed.mechanism_category}|{svc}"


def assign_v6_split(instance_id: str, *, salt: str = SPLIT_SALT) -> str:
    """Three-way hash split used until the full 500-task manifest is frozen."""
    b = _bucket(instance_id, salt)
    if b < 0.70:
        return "train"
    if b < 0.85:
        return "dev"
    return "test"


def build_split_manifest(seeds: list[V5Seed]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    strata: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    assignments: dict[str, str] = {}
    for seed in seeds:
        split = assign_v6_split(seed.instance_id)
        assignments[seed.instance_id] = split
        counts[split] += 1
        strata[strata_key(seed)][split] += 1
    return {
        "salt": SPLIT_SALT,
        "target": {"train": TARGET_TRAIN, "dev": TARGET_DEV, "test": TARGET_TEST},
        "counts": dict(counts),
        "strata": {k: dict(v) for k, v in sorted(strata.items())},
        "assignments": assignments,
    }
