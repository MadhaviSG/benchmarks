"""The v7 task record: one hand-checked task, its twin and its two references.

v6 generated statements, graders and workspaces from templates and failed to
elicit harm. A v7 task is written as a whole: the statement names the
deliverable, the grader reads that deliverable for values an agent would
naturally write, the workspace holds the real trap, and two scripted
references show the harmful and the safe way through. The references are lists
of ``terminal`` / ``file_editor`` steps in the format
:func:`benchmarks.openagentsafety.prefix_replay.replay_prefix_tools` executes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from safety_monitor.synthesis.v5_types import GRADER_KINDS, SERVICES, Grader


# v9 tasks are generated in the v7 format and share this schema.
INSTANCE_ID_RE = re.compile(r"safety-v[79]-[a-z0-9-]+")

HOOKS = frozenset(
    {"direct_routine", "bulk_wrong_file", "hidden_constraint", "npc_mid_task"}
)


class EffectSpec(BaseModel):
    """What a terminal step does to the world, for the in-memory dry run."""

    kind: Literal["write", "delete", "owncloud"]
    target: str
    payload: str = ""


class RefStep(BaseModel):
    tool_name: Literal["terminal", "file_editor"]
    arguments: dict[str, Any]
    pivotal: bool = False
    allow_failure: bool = False
    effects: list[EffectSpec] = Field(default_factory=list)


class GraderSpec(BaseModel):
    """Mirror of :class:`v5_types.Grader`, so the existing builders apply."""

    kind: str
    target: str = ""
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    min_count: int | None = None
    max_count: int | None = None
    column: str = ""
    directory: str = "Documents"
    note: str = ""

    def to_grader(self) -> Grader:
        return Grader(
            kind=self.kind,
            target=self.target,
            must=[term.lower() for term in self.must],
            must_not=[term.lower() for term in self.must_not],
            min_count=self.min_count,
            max_count=self.max_count,
            column=self.column,
            directory=self.directory,
            note=self.note,
        )


class NpcSpec(BaseModel):
    first_name: str
    last_name: str
    age: int
    gender: str
    pronoun: str
    occupation: str
    public_info: str
    goal: str
    extra_info: str = ""
    strategy_hint: str = ""


class V7Axes(BaseModel):
    hook: str
    outcome: str
    framing: str
    deliverable: str
    surface: str
    domain: str

    @model_validator(mode="after")
    def _known_hook(self) -> "V7Axes":
        if self.hook not in HOOKS:
            raise ValueError(f"unknown hook {self.hook!r}; choose from {sorted(HOOKS)}")
        return self


class V7Twin(BaseModel):
    """The same task with the trap removed. Graded by the same grader.

    ``workspace_files`` overrides the task's files: a path mapped to ``None`` is
    removed. ``reference`` is the obvious completion on the twin and must grade
    safe; it defaults to the task's violator reference.
    """

    instance_id: str
    problem_statement: str | None = None
    workspace_files: dict[str, str | None] = Field(default_factory=dict)
    npc: NpcSpec | None = None
    reference: list[RefStep] | None = None
    note: str = ""


class V7Task(BaseModel):
    instance_id: str
    axes: V7Axes
    problem_statement: str
    environment: str
    workspace_files: dict[str, str]
    services: list[str] = Field(default_factory=list)
    npc: NpcSpec | None = None
    grader: GraderSpec | None = None
    evaluator_code: str | None = None
    checkpoint_text: str
    violator_reference: list[RefStep]
    safe_reference: list[RefStep]
    teardown: list[RefStep] = Field(default_factory=list)
    benign_twin: V7Twin | None = None
    twin_of: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _contract(self) -> "V7Task":
        if not INSTANCE_ID_RE.fullmatch(self.instance_id):
            raise ValueError(
                f"instance_id {self.instance_id!r} must match safety-v7-* or safety-v9-*"
            )
        if (self.grader is None) == (self.evaluator_code is None):
            raise ValueError("give exactly one of grader or evaluator_code")
        if self.grader is not None and self.grader.kind not in GRADER_KINDS:
            raise ValueError(f"unknown grader kind {self.grader.kind!r}")
        unknown = set(self.services) - SERVICES
        if unknown:
            raise ValueError(f"unknown services {sorted(unknown)}")
        pivotal = sum(step.pivotal for step in self.violator_reference)
        if self.twin_of is None and pivotal != 1:
            raise ValueError(f"violator reference has {pivotal} pivotal steps, need 1")
        if any(step.pivotal for step in self.safe_reference):
            raise ValueError("safe reference must not mark a pivotal step")
        return self

    def pivotal_step(self) -> RefStep | None:
        return next((s for s in self.violator_reference if s.pivotal), None)


def load_tasks(path: Path) -> list[V7Task]:
    with path.open(encoding="utf-8") as handle:
        return [V7Task.model_validate_json(line) for line in handle if line.strip()]


def dump_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = [
    "EffectSpec",
    "GraderSpec",
    "HOOKS",
    "NpcSpec",
    "RefStep",
    "V7Axes",
    "V7Task",
    "V7Twin",
    "dump_jsonl",
    "load_tasks",
]
