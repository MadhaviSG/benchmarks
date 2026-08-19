"""Tests for the deterministic v5 synthetic pair generator."""

from __future__ import annotations

import re

import pytest
from safety_monitor.synthesis.v5_generate import (
    HEADER_RE,
    build_pair,
    build_pair_record,
    build_trajectory,
    load_seeds,
    render,
    replay_grader,
    schedule,
    validate_pair,
)
from safety_monitor.synthesis.v5_graders import synthesize_evaluator
from safety_monitor.synthesis.v5_types import Act
from safety_monitor.types import SafetyLabel


SEEDS = load_seeds()
PLAN = schedule(SEEDS)


def _rendered_pairs():
    out = []
    for seed in SEEDS:
        variant, target = PLAN[seed.instance_id]
        out.append(build_pair(seed, variant, target))
    return out


def test_sixty_authored_seeds():
    assert len(SEEDS) == 60
    assert len({s.instance_id for s in SEEDS}) == 60
    assert len({s.seed_id for s in SEEDS}) == 60


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_each_pair_validates(seed):
    variant, target = PLAN[seed.instance_id]
    validate_pair(build_pair(seed, variant, target))


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_prefix_is_byte_identical_and_single_divergence(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    assert rp.unsafe_text.startswith(rp.prefix_text)
    assert rp.safe_text.startswith(rp.prefix_text)
    assert rp.unsafe_text != rp.safe_text
    i = 0
    limit = min(len(rp.unsafe_text), len(rp.safe_text))
    while i < limit and rp.unsafe_text[i] == rp.safe_text[i]:
        i += 1
    assert i >= len(rp.prefix_text)


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_action_counts_equal_and_in_range(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    n_u = len(rp.unsafe_events)
    n_s = len(rp.safe_events)
    assert n_u == n_s
    assert 8 <= n_u <= 24


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_exactly_one_pivotal_in_unsafe_none_in_safe(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    assert sum(1 for e in rp.unsafe_acts if e.pivotal) == 1
    assert sum(1 for e in rp.safe_acts if e.pivotal) == 0


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_only_terminal_and_file_editor_tools(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    for text in (rp.unsafe_text, rp.safe_text):
        tools = set(re.findall(r"Tool: (\w+)", text))
        assert tools <= {"terminal", "file_editor"}


def test_headers_match_corpus_regex():
    for rp in _rendered_pairs():
        for text in (rp.unsafe_text, rp.safe_text):
            headers = re.findall(r"^\[Step \d+\].*$", text, re.MULTILINE)
            assert headers, rp.seed.instance_id
            for header in headers:
                assert HEADER_RE.match(header), header
            numbers = [
                int(n) for n in re.findall(r"^\[Step (\d+)\]", text, re.MULTILINE)
            ]
            assert numbers == list(range(1, len(numbers) + 1))


def test_terminal_and_file_editor_action_lines():
    text = render(
        "Do it.\n\nBegin!",
        [
            Act(
                kind="terminal",
                summary="s",
                thought="t",
                command="ls -la",
                observation="out",
            ),
            Act(
                kind="file_editor",
                summary="s",
                thought="t",
                command='create path=/x content="""hi"""',
                observation="File created.",
            ),
        ],
    )
    assert "[TerminalAction] cmd=ls -la is_input=False reset=False" in text
    assert "[FileEditorAction] cmd=create path=/x" in text


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_labeled_steps_localize_the_pivotal_action(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    harmful = build_trajectory(rp, "harmful", "train")
    safe = build_trajectory(rp, "clean", "train")
    assert harmful.high_unsafe_count == 1
    assert safe.high_unsafe_count == 0
    high = [s for s in harmful.steps if s.label is SafetyLabel.HIGH_UNSAFE]
    assert len(high) == 1
    assert harmful.rule_based == 1 and safe.rule_based == 0


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.instance_id)
def test_emitted_grader_separates_halves(seed):
    variant, target = PLAN[seed.instance_id]
    rp = build_pair(seed, variant, target)
    code = synthesize_evaluator(seed)
    replay = replay_grader(seed, rp, code)
    assert replay["ok"], replay


def test_pair_record_references_both_halves():
    rp = _rendered_pairs()[0]
    harmful = build_trajectory(rp, "harmful", "train")
    safe = build_trajectory(rp, "clean", "train")
    pair = build_pair_record(rp, harmful, safe)
    assert pair.harmful_key == harmful.key
    assert pair.safe_key == safe.key
    assert pair.harmful_high_unsafe == 1
    assert pair.instance_id == rp.seed.instance_id
