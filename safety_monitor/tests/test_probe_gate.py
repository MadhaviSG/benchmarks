"""Tests for the shallow-probe classifier leakage gate."""

from __future__ import annotations

import copy
import hashlib

import pytest
from safety_monitor.synthesis.pairs import CLEAN, HARMFUL, LabeledStep, MinedTrajectory
from safety_monitor.synthesis.probe_gate import (
    PROBE_AUROC_MAX,
    build_post_divergence_document,
    divergence_index,
    group_kfold_assignments,
    pair_trajectories,
    run_probe_gate,
)
from safety_monitor.synthesis.v6_validate import gate_probe
from safety_monitor.types import SafetyLabel


def _step(
    action_id: str,
    command: str,
    *,
    summary: str | None = None,
    observation: str = "ok",
) -> LabeledStep:
    return LabeledStep(
        action_id=action_id,
        tool_name="terminal",
        arguments={"command": command},
        summary=summary or command[:40],
        observation=observation,
        label=SafetyLabel.LOW_UNSAFE,
        label_source="test",
    )


def _traj(instance_id: str, role: str, steps: list[LabeledStep]) -> MinedTrajectory:
    return MinedTrajectory(
        key=f"test::{instance_id}::{role}",
        instance_id=instance_id,
        run="test",
        condition="synthetic",
        role=role,
        steps=steps,
        split="train",
    )


def _shared_prefix(n: int = 4) -> list[LabeledStep]:
    return [
        _step(f"shared:{i}", f"echo shared-step-{i}", observation=f"out-{i}")
        for i in range(n)
    ]


def _make_pair(
    pair_idx: int,
    *,
    harmful_tail: str,
    clean_tail: str,
    clean_marker: str | None = None,
    neutral_pivot: bool = False,
) -> list[MinedTrajectory]:
    iid = f"safety-test-pair-{pair_idx:03d}"
    prefix = _shared_prefix()
    if neutral_pivot:
        pivot_summary = "Apply the operational change"
        pivot_obs = f"artifact updated for task {pair_idx}"
        tail_summary = "Confirm workspace state"
        tail_obs = "directory listing captured"
        harmful_steps = prefix + [
            _step(
                f"{iid}:harmful:pivot",
                harmful_tail,
                summary=pivot_summary,
                observation=pivot_obs,
            ),
            _step(
                f"{iid}:harmful:tail",
                "ls -la /workspace",
                summary=tail_summary,
                observation=tail_obs,
            ),
        ]
        clean_steps = prefix + [
            _step(
                f"{iid}:clean:pivot",
                clean_tail,
                summary=pivot_summary,
                observation=pivot_obs,
            ),
            _step(
                f"{iid}:clean:tail",
                "ls -la /workspace",
                summary=tail_summary,
                observation=tail_obs,
            ),
        ]
    else:
        harmful_steps = prefix + [
            _step(
                f"{iid}:harmful:pivot",
                harmful_tail,
                summary=f"harmful pivot {pair_idx}",
                observation=f"harmful outcome {pair_idx}",
            ),
            _step(
                f"{iid}:harmful:tail",
                "echo done-harmful",
                observation="finished harmful",
            ),
        ]
        clean_obs = clean_marker or f"safe outcome {pair_idx}"
        clean_steps = prefix + [
            _step(
                f"{iid}:clean:pivot",
                clean_tail,
                summary=f"safe pivot {pair_idx}",
                observation=clean_obs,
            ),
            _step(
                f"{iid}:clean:tail",
                "echo done-safe",
                observation="finished safe",
            ),
        ]
    return [
        _traj(iid, HARMFUL, harmful_steps),
        _traj(iid, CLEAN, clean_steps),
    ]


def _no_leak_corpus(n_pairs: int = 30) -> list[MinedTrajectory]:
    trajs: list[MinedTrajectory] = []
    for i in range(n_pairs):
        iid = f"safety-test-pair-{i:03d}"
        flip = int(hashlib.sha1(iid.encode()).hexdigest()[:8], 16) % 2
        token = f"{i:04x}"
        harmful_suffix, clean_suffix = ("a", "b") if flip else ("b", "a")
        trajs.extend(
            _make_pair(
                i,
                harmful_tail=f"touch /workspace/out/{token}{harmful_suffix}.dat",
                clean_tail=f"touch /workspace/out/{token}{clean_suffix}.dat",
                neutral_pivot=True,
            )
        )
    return trajs


def _planted_leak_corpus(n_pairs: int = 30) -> list[MinedTrajectory]:
    trajs: list[MinedTrajectory] = []
    for i in range(n_pairs):
        trajs.extend(
            _make_pair(
                i,
                harmful_tail=f"printf 'deploy harmful variant {i}'",
                clean_tail=f"printf 'verify safe variant {i}'",
                clean_marker="zz_safemark confirms clean half",
            )
        )
    return trajs


def test_divergence_index_finds_first_argument_mismatch():
    harmful = [_step("h:0", "echo a"), _step("h:1", "echo harmful")]
    clean = [_step("c:0", "echo a"), _step("c:1", "echo safe")]
    assert divergence_index(harmful, clean) == 1


def test_group_kfold_keeps_pair_halves_together():
    trajs = _no_leak_corpus(n_pairs=25)
    pairs = pair_trajectories(trajs)
    _, labels, groups = _prepare_from_pairs(pairs)
    assignments = group_kfold_assignments(groups)
    for instance_id, harmful, clean in pairs:
        harmful_doc = build_post_divergence_document(
            harmful.steps, divergence_index(harmful.steps, clean.steps)
        )
        clean_doc = build_post_divergence_document(
            clean.steps, divergence_index(harmful.steps, clean.steps)
        )
        assert harmful_doc != clean_doc
        assert assignments[instance_id] == assignments[instance_id]


def _prepare_from_pairs(pairs):
    from safety_monitor.synthesis.probe_gate import _prepare_corpus

    return _prepare_corpus(pairs)


def test_no_leak_fixture_passes_gate():
    report = run_probe_gate(_no_leak_corpus())
    assert report["ok"]
    assert report["word_probe"]["mean_auroc"] <= PROBE_AUROC_MAX
    assert report["char_probe"]["mean_auroc"] <= PROBE_AUROC_MAX
    assert gate_probe(_no_leak_corpus()).ok


def test_planted_leak_fails_gate_with_marker_in_top_features():
    trajs = _planted_leak_corpus()
    report = run_probe_gate(trajs)
    assert not report["ok"]
    assert report["word_probe"]["mean_auroc"] > 0.95
    top = [row["feature"] for row in report["word_probe"]["top_features"]]
    assert any("zz_safemark" in feature for feature in top)
    assert not gate_probe(trajs).ok


def test_group_kfold_integrity_all_halves_same_fold():
    trajs = _no_leak_corpus(n_pairs=40)
    pairs = pair_trajectories(trajs)
    docs, labels, groups = _prepare_from_pairs(pairs)
    del docs, labels
    assignments = group_kfold_assignments(groups)
    by_instance: dict[str, list[int]] = {}
    for group in groups:
        by_instance.setdefault(group, []).append(assignments[group])
    for folds in by_instance.values():
        assert len(set(folds)) == 1


@pytest.mark.parametrize("n_pairs", [12, 20])
def test_planted_leak_auroc_high(n_pairs: int):
    report = run_probe_gate(_planted_leak_corpus(n_pairs=n_pairs))
    assert report["word_probe"]["mean_auroc"] > 0.95


def test_pair_trajectories_requires_both_halves():
    trajs = _no_leak_corpus(n_pairs=1)
    lone = copy.deepcopy(trajs[0])
    with pytest.raises(ValueError, match="expected harmful and clean"):
        pair_trajectories([lone])
