"""Tests for the v6 synthetic pair generator."""

from __future__ import annotations

import pytest
from safety_monitor.synthesis.v5_generate import replay_grader, validate_pair
from safety_monitor.synthesis.v5_graders import synthesize_evaluator
from safety_monitor.synthesis.v6_content_a import SEEDS as CONTENT_A
from safety_monitor.synthesis.v6_content_b import SEEDS as CONTENT_B
from safety_monitor.synthesis.v6_content_c import SEEDS as CONTENT_C
from safety_monitor.synthesis.v6_generate import (
    build_pair,
    generate,
    load_seeds,
    schedule,
)
from safety_monitor.synthesis.v6_types import validate_v6_all
from safety_monitor.synthesis.v6_validate import (
    CORPUS_JACCARD_MAX,
    LEAKAGE_TOKEN_MAX_RATIO,
    gate_corpus_jaccard,
    gate_grader,
    gate_leakage,
    gate_structural,
)


SEEDS = load_seeds(include_bootstrap=True, include_content=True, grid_fill=0)
PLAN = schedule(SEEDS)
CONTENT = CONTENT_A + CONTENT_B + CONTENT_C


def test_hand_authored_content_modules_present():
    """v6 must ship deterministic content modules, not LLM-only bootstrap."""
    assert len(CONTENT_A) >= 40
    assert len(CONTENT_B) >= 40
    assert len(CONTENT_C) >= 40
    assert len(CONTENT) >= 200


def test_v6_target_task_count_load():
    """Content + bootstrap should yield at least 550 accepted v6 seeds."""
    from safety_monitor.synthesis.v6_validate import (
        load_existing_corpus_problems,
        validate_seed_gates,
    )

    existing = load_existing_corpus_problems()
    accepted = 0
    for seed in load_seeds(include_bootstrap=True, include_content=True, grid_fill=0):
        report = validate_seed_gates(seed, existing_problems=existing)
        if report.accepted:
            accepted += 1
            existing.append(
                seed.title + " " + " ".join(seed.context) + " " + seed.seed_sentence
            )
            if accepted >= 550:
                break
    assert accepted == 550


def test_content_modules_use_v6_instance_ids():
    for seed in CONTENT[:20]:
        assert seed.instance_id.startswith("safety-v6-")


def test_at_least_hundred_accepted_seeds_load():
    """Content + bootstrap should yield a triple-digit v6 slice."""
    assert len(SEEDS) >= 100


@pytest.mark.parametrize(
    "seed", SEEDS[: min(10, len(SEEDS))], ids=lambda s: s.instance_id
)
def test_v6_instance_ids(seed):
    assert seed.instance_id.startswith("safety-v6-")


@pytest.mark.parametrize(
    "seed", SEEDS[: min(10, len(SEEDS))], ids=lambda s: s.instance_id
)
def test_structural_and_grader_gates(seed):
    assert gate_structural(seed).ok
    assert gate_grader(seed).ok


@pytest.mark.parametrize(
    "seed", SEEDS[: min(5, len(SEEDS))], ids=lambda s: s.instance_id
)
def test_pair_replay(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    validate_pair(rp)
    code = synthesize_evaluator(seed)
    replay = replay_grader(seed, rp, code)
    assert replay["ok"], replay


def test_no_contract_violations_in_content():
    problems = validate_v6_all(CONTENT)
    assert not problems, problems[:5]


def test_no_contract_violations_in_load():
    problems = validate_v6_all(SEEDS)
    assert not problems, problems[:5]


def test_end_to_end_generate_smoke(tmp_path):
    summary = generate(
        pairs_dir=tmp_path / "pairs",
        tasks_dir=tmp_path / "tasks",
        include_bootstrap=True,
        include_content=True,
        grid_fill=25,
        max_tasks=550,
        write_report=False,
    )
    assert summary["n_tasks"] == 550
    assert summary["n_trajectories"] == summary["n_tasks"] * 2
    assert summary["replay"]["n_failed"] == 0
    assert summary["mean_pairwise_jaccard_problem_statements"] <= CORPUS_JACCARD_MAX
    assert all(g["ok"] for g in summary["corpus_gates"])
    assert (tmp_path / "pairs" / "trajectories.jsonl").exists()
    legacy = (
        (tmp_path / "pairs" / "safety_trajectories_v6_synthetic.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(legacy) == summary["n_tasks"] * 2


def test_leakage_gate_rejects_deliberately_leaky_pair():
    from safety_monitor.synthesis.v5_generate import RenderedPair
    from safety_monitor.synthesis.v5_types import Grader, V5Seed

    seed = V5Seed(
        seed_id=99999,
        instance_id="safety-v6-leak-test",
        outcome_category="Harmful decision-making",
        mechanism_category="A",
        tool="file system",
        intent="benign",
        seed_sentence="Leak test seed.",
        gradeability="state_change",
        tool_realization="file system -> file editor plus terminal",
        harm_kind="commission",
        services=["filesystem-only"],
        environment="Test workspace.",
        title="Leak test",
        context=["Synthetic leak test."],
        steps=["do the thing"],
        closing="",
        variant=0,
        grader=Grader(kind="file_contains", target="/workspace/x.txt"),
        checkpoint_text="leak test",
    )
    prefix = "[Step 1] USER\n" + "-" * 40 + "\nMessage: test\n"
    leaky = RenderedPair(
        seed=seed,
        problem_statement="test",
        instruction="test",
        prefix=[],
        unsafe_acts=[],
        safe_acts=[],
        prefix_text=prefix,
        unsafe_text=prefix + "harmful-only-token appears here verbatim",
        safe_text=prefix + "completely different safe-only wording throughout",
        variant=0,
        target_actions=8,
    )
    result = gate_leakage([leaky], max_ratio=LEAKAGE_TOKEN_MAX_RATIO)
    assert not result.ok
    assert result.gate == "leakage"


def test_corpus_jaccard_gate_rejects_templated_batch():
    templated = [
        "You are supporting Acme Corp. While supporting Acme Corp the agent does X.\n\n"
        "What this needs:\n\n1. Read controls\n2. Apply change"
        for _ in range(20)
    ]
    result = gate_corpus_jaccard(templated, max_jaccard=CORPUS_JACCARD_MAX)
    assert not result.ok
    assert result.gate == "corpus_jaccard"
