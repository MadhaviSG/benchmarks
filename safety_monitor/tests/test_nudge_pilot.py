"""Arm assignment and reporting for the bidirectional nudge pilot."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_RAS = Path(__file__).resolve().parents[3]
_MODULE_PATH = _RAS / "analysis_outputs" / "prefix_resample" / "run_nudge_pilot.py"

if not _MODULE_PATH.exists():  # pragma: no cover - checkout without artifacts
    pytest.skip("nudge pilot script not present", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("run_nudge_pilot", _MODULE_PATH)
assert _spec and _spec.loader
np_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(np_mod)


def _control_file(tmp_path: Path, outcomes: dict[str, list[bool | None]]) -> Path:
    path = tmp_path / "continuations.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for instance_id, results in outcomes.items():
            for unsafe in results:
                handle.write(
                    json.dumps({"instance_id": instance_id, "grader_unsafe": unsafe})
                    + "\n"
                )
    return path


def test_all_safe_tasks_get_harm_directed_arms(tmp_path: Path) -> None:
    path = _control_file(tmp_path, {"t-safe": [False, False, False, False]})

    plan = np_mod.plan_arms(np_mod.control_outcomes(path))

    assert len(plan) == 1
    assert plan[0]["direction"] == "toward_harm"
    assert plan[0]["arms"] == ["overt", "pressure"]
    assert plan[0]["moved_means"] == "unsafe"


def test_all_unsafe_tasks_get_safety_directed_arms(tmp_path: Path) -> None:
    path = _control_file(tmp_path, {"t-unsafe": [True, True, True, True]})

    plan = np_mod.plan_arms(np_mod.control_outcomes(path))

    assert plan[0]["direction"] == "toward_safety"
    assert plan[0]["arms"] == ["safety", "refusal"]
    assert plan[0]["moved_means"] == "safe"


def test_splitting_tasks_are_excluded(tmp_path: Path) -> None:
    """A prefix that already reaches both outcomes needs no intervention."""
    path = _control_file(
        tmp_path,
        {
            "t-split": [False, False, True, True],
            "t-fixed": [True, True, True, True],
        },
    )

    plan = np_mod.plan_arms(np_mod.control_outcomes(path))

    assert [e["instance_id"] for e in plan] == ["t-fixed"]


def test_pointed_arm_is_never_assigned(tmp_path: Path) -> None:
    path = _control_file(
        tmp_path,
        {"t-safe": [False, False], "t-unsafe": [True, True]},
    )

    plan = np_mod.plan_arms(np_mod.control_outcomes(path))

    assert all("pointed" not in e["arms"] for e in plan)


def test_errored_control_rows_do_not_count_as_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "continuations.jsonl"
    path.write_text(
        json.dumps({"instance_id": "t", "grader_unsafe": None, "error": "boom"})
        + "\n"
        + json.dumps({"instance_id": "t", "grader_unsafe": True})
        + "\n",
        encoding="utf-8",
    )

    assert np_mod.control_outcomes(path) == {"t": [True]}


def test_summary_counts_a_task_as_moved_on_one_flip() -> None:
    plan = [
        {
            "instance_id": "t-unsafe",
            "control_unsafe": [True, True],
            "control_outcome": "unsafe",
            "arms": ["safety", "refusal"],
            "direction": "toward_safety",
            "moved_means": "safe",
        }
    ]
    rows = [
        {"instance_id": "t-unsafe", "grader_unsafe": True, "induced_nudge": "safety"},
        {"instance_id": "t-unsafe", "grader_unsafe": False, "induced_nudge": "refusal"},
    ]

    summary = np_mod.summarise(plan, rows)

    assert summary["n_tasks_moved"] == 1
    assert summary["tasks"][0]["n_moved"] == 1
    assert summary["tasks"][0]["moved_by"] == ["refusal"]


def test_summary_reports_tasks_not_continuations() -> None:
    plan = [
        {
            "instance_id": f"t{i}",
            "control_unsafe": [False],
            "control_outcome": "safe",
            "arms": ["overt", "pressure"],
            "direction": "toward_harm",
            "moved_means": "unsafe",
        }
        for i in range(4)
    ]
    rows = [
        {"instance_id": "t0", "grader_unsafe": True, "induced_nudge": "overt"},
        {"instance_id": "t0", "grader_unsafe": True, "induced_nudge": "pressure"},
    ]

    summary = np_mod.summarise(plan, rows)

    assert summary["n_tasks"] == 4
    assert summary["n_tasks_moved"] == 1, "two flips on one task is one task"
    assert summary["unit_of_report"] == "task"


def test_errored_nudge_rows_are_not_counted_as_unmoved() -> None:
    plan = [
        {
            "instance_id": "t",
            "control_unsafe": [True],
            "control_outcome": "unsafe",
            "arms": ["safety"],
            "direction": "toward_safety",
            "moved_means": "safe",
        }
    ]
    rows = [{"instance_id": "t", "error": "boom", "grader_unsafe": None}]

    summary = np_mod.summarise(plan, rows)

    assert summary["tasks"][0]["n_nudged"] == 0
    assert summary["tasks"][0]["n_errors"] == 1
    assert summary["tasks"][0]["moved"] is False
