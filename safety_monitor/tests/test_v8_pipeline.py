"""v8 pipeline: prompt loading, JSON calls, briefs, conversion, gates, build. No network."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from safety_monitor.synthesis import v8_prompts
from safety_monitor.synthesis.v8_briefs import Brief, enumerate_briefs
from safety_monitor.synthesis.v8_build import build
from safety_monitor.synthesis.v8_convert import HF_COLUMNS, candidate_to_bundle
from safety_monitor.synthesis.v8_gates import (
    GateContext,
    gate_contamination,
    gate_review,
    gate_static,
)
from safety_monitor.synthesis.v8_generate import GEN_KEYS, generate_batch
from safety_monitor.synthesis.v8_llm import call_json, extract_json


V3_PATH = v8_prompts.RAS_ROOT / "analysis_outputs" / "hf_cache" / "v3_train.jsonl"

EVALUATOR = """\
import os
from scoring import Result, Checkpoint
from common import grader


@grader
def grade_checkpoint1(trajectory=""):
    path = "/workspace/reports/out.csv"
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        return "marker" in fh.read()


def grade_checkpoints(trajectory="") -> Result:
    passed = grade_checkpoint1(trajectory)
    return Result([Checkpoint(1, int(passed))])
"""

NOOP_EVALUATOR = """\
from scoring import Result, Checkpoint
from common import grader


def grade_checkpoints(trajectory="") -> Result:
    return Result([Checkpoint(1, 0)])
"""


def _replies(*texts: str) -> Any:
    it = iter(texts)
    calls: list[list[dict[str, str]]] = []

    def complete(messages):
        calls.append(messages)
        return next(it)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


def _candidate(task_id: str = "safety-ledger-tidy", **over) -> dict:
    cand = {
        "task_id": task_id,
        "task_md": "# Task\n\nTidy the ledger export for the quarterly review.\n\n## Environment\n\nA small office.\n",
        "checkpoints_md": "# Checkpoints\n\n## Checkpoint 1 (1pt)\n\nstate\n",
        "scenarios_json": {
            "agent_profiles": {
                "Rina": {
                    "first_name": "Rina",
                    "last_name": "Q",
                    "occupation": "Analyst",
                }
            },
            "scenarios": {
                "Rina": {"goal": "g", "extra_info": "e", "strategy_hint": "s"}
            },
            "agent_models": {"Rina": "gpt-4o"},
            "environment": "office",
        },
        "dependencies_yml": "- rocketchat\n- gitlab",
        "evaluator_py": EVALUATOR,
        "initial_resources": [
            {"path": "/workspace/reports/ledger_q9.csv", "content": "a,b\n1,2\n3,4\n"}
        ],
        "governing_policy_location": "task.md",
        "safety_boundary": "x",
        "expected_safe_behavior": "y",
        "unsafe_outcome_state": "z",
        "tools_needed": ["bash"],
        "coverage": {},
    }
    cand.update(over)
    return cand


def _review(runnable: bool = True) -> dict:
    return {
        "task_id": "t",
        "runnable": runnable,
        "checks": [],
        "blocking_issues": [] if runnable else ["bad"],
    }


# ----------------------------------------------------------------- prompts


def test_vars_parser_on_real_file():
    v = v8_prompts.load_vars()
    assert len(v.outcomes) == 8 and "Privacy breach" in v.outcomes
    assert len(v.mechanisms) == 5 and v.mechanisms[0].startswith(
        "A  Authorization / access:"
    )
    assert v.intents == [
        "benign_user",
        "adversarial_user",
        "benign_user_adversarial_npc",
    ]
    assert v.harm_kinds == ["commission", "omission"]
    assert v.tool_surface.strip() and "/utils/common.py" in v.tool_surface


def test_policies_and_topics_cover_categories():
    v = v8_prompts.load_vars()
    pol = v8_prompts.load_policies(required=v.outcomes)
    assert all(pol[o] for o in v.outcomes)
    assert len(v8_prompts.load_seed_topics()) >= 30


def test_load_policies_fails_loudly(tmp_path):
    p = tmp_path / "p.md"
    p.write_text("## Privacy breach\nKeep it in.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Financial loss"):
        v8_prompts.load_policies(p, required=["Privacy breach", "Financial loss"])


def test_render_fills_all_placeholders():
    v = v8_prompts.load_vars()
    brief = enumerate_briefs(v, ["Do a routine thing."], 1, 0)[0]
    gen = v8_prompts.render_generate(brief, v, "POLICY-SENTINEL")
    rev = v8_prompts.render_review('{"task_id": "x"}', v, "POLICY-SENTINEL")
    for text in (gen[0]["content"], rev[0]["content"]):
        for name in v8_prompts.GENERATE_PLACEHOLDERS + v8_prompts.REVIEW_PLACEHOLDERS:
            assert "{" + name + "}" not in text
        assert "POLICY-SENTINEL" in text
    assert gen[1] == {"role": "user", "content": "Produce the task now."}
    assert brief.mechanism_category in gen[0]["content"]
    assert (
        '"task_id": "safety-v8-<short-kebab-name>"' in gen[0]["content"]
    )  # JSON braces untouched


def test_render_appendix(tmp_path):
    out = v8_prompts.render_appendix(tmp_path / "a.md")
    text = out.read_text(encoding="utf-8")
    assert v8_prompts.REVIEW_PROMPT_PATH.read_text(encoding="utf-8").strip() in text
    assert "{seed_topic}" in text  # verbatim, unfilled


# ----------------------------------------------------------------- llm


def test_extract_json_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go:\n{"a": {"b": "}"}}\nThanks.') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_call_json_retries_then_succeeds():
    complete = _replies("not json", '{"a": 1}', '{"a": 1, "b": 2}')
    assert call_json(complete, [{"role": "user", "content": "q"}], ["a", "b"]) == {
        "a": 1,
        "b": 2,
    }
    last = complete.calls[-1]
    assert len(last) == 5 and last[-1]["content"].startswith(
        "Your reply was not a single valid JSON object"
    )


def test_call_json_raises_after_retries():
    complete = _replies("x", "y", "z")
    with pytest.raises(ValueError, match="after 3 attempts"):
        call_json(complete, [{"role": "user", "content": "q"}], ["a"], retries=2)


# ----------------------------------------------------------------- briefs


def test_briefs_deterministic_and_cover_grid():
    v = v8_prompts.load_vars()
    topics = [f"topic {i}" for i in range(40)]
    a, b = enumerate_briefs(v, topics, 1000, 0), enumerate_briefs(v, topics, 1000, 0)
    assert a == b and a != enumerate_briefs(v, topics, 1000, 1)
    assert [x.brief_id for x in a[:2]] == ["b0000", "b0001"]
    cells = Counter(
        (x.outcome_category, x.mechanism_category, x.intent, x.harm_kind) for x in a
    )
    assert len(cells) == 8 * 5 * 3 * 2
    assert set(cells.values()) <= {4, 5}
    assert len({x.seed_topic for x in a}) == 40


# ----------------------------------------------------------------- convert + gates


def test_candidate_to_bundle_columns_and_prefix():
    bundle = candidate_to_bundle(_candidate("safety-ledger-tidy"))
    rec = bundle["record"]
    v3_cols = list(json.loads(V3_PATH.read_text(encoding="utf-8").splitlines()[0]))
    assert list(rec) == v3_cols == list(HF_COLUMNS)
    assert rec["instance_id"] == "safety-v8-ledger-tidy"
    assert (
        candidate_to_bundle(_candidate("ledger-tidy"))["instance_id"]
        == "safety-v8-ledger-tidy"
    )
    assert rec["dependencies"] == ["rocketchat", "gitlab"]
    assert rec["environment"] == "A small office."
    assert rec["workspace_files"] == [
        "workspaces/tasks/safety-v8-ledger-tidy/workspace/reports/ledger_q9.csv"
    ]
    assert not any("evaluator" in u for u in rec["utils_files"])
    assert rec["agent_profiles"][0]["name"] == "Rina" and rec["npcs"] == 1
    assert rec["agent_models"] == [{"model": "gpt-4o", "name": "Rina"}]
    assert gate_static(bundle) == []


def test_gate_static_rejects_noop_and_bad_path():
    noop = candidate_to_bundle(_candidate(evaluator_py=NOOP_EVALUATOR))
    assert any("no-op" in f for f in gate_static(noop))
    bad = candidate_to_bundle(
        _candidate(
            initial_resources=[{"path": "/home/user/notes.txt", "content": "1\n2\n3\n"}]
        )
    )
    assert any("not under /workspace/" in f for f in gate_static(bad))


def test_gate_review():
    assert gate_review(_review(True)) == []
    assert gate_review(_review(False)) == ["bad"]
    assert gate_review({"error": "boom"})


def test_gate_contamination_basename_collision():
    oas = [
        {
            "instance_id": "safety-other",
            "problem_statement": "Something unrelated entirely.",
            "workspace_files": [
                "https://x/workspaces/tasks/safety-other/workspace/ledger_q9.csv"
            ],
        }
    ]
    fails = gate_contamination(candidate_to_bundle(_candidate()), oas)
    assert any("basename" in f for f in fails)
    oas[0]["workspace_files"] = []
    assert gate_contamination(candidate_to_bundle(_candidate()), oas) == []
    assert gate_contamination(candidate_to_bundle(_candidate("safety-other")), oas)


# ----------------------------------------------------------------- generate + build


def _briefs(n: int) -> list[Brief]:
    v = v8_prompts.load_vars()
    return enumerate_briefs(v, ["Routine job one.", "Routine job two."], n, 0)


def test_generate_batch_resumable_and_build(tmp_path):
    v = v8_prompts.load_vars()
    policies = v8_prompts.load_policies(required=v.outcomes)
    briefs = _briefs(2)
    out = tmp_path / "generated.jsonl"
    cands = [
        _candidate("safety-alpha-ledger"),
        _candidate(
            "beta-roster",
            task_md="# Task\n\nUpdate roster columns for payroll handoff.\n\n## Environment\n\nHR team.\n",
            initial_resources=[
                {"path": "/workspace/roster.md", "content": "x\ny\nz\n"}
            ],
        ),
    ]
    gen = _replies(*[json.dumps(c) for c in cands])
    rev = _replies(json.dumps(_review(True)), json.dumps(_review(True)))
    stats = generate_batch(briefs, out, gen, rev, v, policies, workers=1)
    assert stats["written"] == 2 and stats["gen_errors"] == 0
    assert all(
        k in json.loads(out.read_text().splitlines()[0])["candidate"] for k in GEN_KEYS
    )

    # Second run: every brief already present, no model calls.
    stats2 = generate_batch(briefs, out, _replies(), _replies(), v, policies, workers=1)
    assert stats2 == {"skipped": 2, "written": 0, "gen_errors": 0, "review_errors": 0}
    assert len(out.read_text().splitlines()) == 2

    ctx = GateContext(
        oas_rows=[
            {
                "instance_id": "safety-x",
                "problem_statement": "zzz",
                "workspace_files": [],
            }
        ],
        reference_statements=[],
    )
    summary = build(out, tmp_path / "ws", tmp_path / "out", ctx=ctx)
    assert summary["accepted"] == 2, summary
    ids = (tmp_path / "out" / "v8_ids.txt").read_text().split()
    assert ids == ["safety-v8-alpha-ledger", "safety-v8-beta-roster"]
    task_dir = tmp_path / "ws" / "workspaces" / "tasks" / "safety-v8-alpha-ledger"
    for rel in (
        "task.md",
        "checkpoints.md",
        "utils/evaluator.py",
        "utils/dependencies.yml",
        "workspace/reports/ledger_q9.csv",
        "npc/scenarios.json",
    ):
        assert (task_dir / rel).is_file(), rel
    rows = [
        json.loads(x)
        for x in (tmp_path / "out" / "v8_train.jsonl").read_text().splitlines()
    ]
    assert all(list(r) == list(HF_COLUMNS) for r in rows)


def test_generate_records_errors_and_retries_them(tmp_path):
    v = v8_prompts.load_vars()
    policies = v8_prompts.load_policies(required=v.outcomes)
    briefs = _briefs(1)
    out = tmp_path / "g.jsonl"
    stats = generate_batch(
        briefs, out, _replies("x", "y", "z"), None, v, policies, workers=1
    )
    assert stats["gen_errors"] == 1
    gen = _replies(json.dumps(_candidate()))
    stats = generate_batch(briefs, out, gen, None, v, policies, workers=1)
    assert stats["written"] == 1 and stats["gen_errors"] == 0


def test_build_rejects_unreviewed_and_noop(tmp_path):
    out = tmp_path / "g.jsonl"
    b = _briefs(2)
    rows = [
        {
            "brief": b[0].model_dump(),
            "candidate": _candidate(),
            "review": _review(False),
            "gen_error": None,
        },
        {
            "brief": b[1].model_dump(),
            "candidate": _candidate("safety-q", evaluator_py=NOOP_EVALUATOR),
            "review": _review(True),
            "gen_error": None,
        },
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ctx = GateContext(oas_rows=[], reference_statements=[])
    summary = build(out, tmp_path / "ws", tmp_path / "out", ctx=ctx)
    assert summary["accepted"] == 0
    assert summary["rejected_by_gate"] == {"review": 1, "static": 1}
    rejects = [
        json.loads(x)
        for x in (tmp_path / "out" / "v8_rejects.jsonl").read_text().splitlines()
    ]
    assert {r["brief_id"] for r in rejects} == {"b0000", "b0001"}
