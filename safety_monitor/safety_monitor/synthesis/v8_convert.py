"""Convert a v8 generation candidate into an OAS bundle and an HF-schema record."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v5_generate import REPO_PREFIX


ID_PREFIX = "safety-v8-"
DEFAULT_ENVIRONMENT = "The Agent Company workplace sandbox with a /workspace directory and company services."
HF_COLUMNS = (
    "instance_id",
    "problem_statement",
    "dependencies",
    "checkpoints",
    "agent_profiles",
    "agent_scenarios",
    "agent_models",
    "environment",
    "npcs",
    "has_workspace",
    "workspace_files",
    "has_utils",
    "utils_files",
    "evaluator_code",
    "github_url",
)


def normalize_instance_id(task_id: str) -> str:
    slug = str(task_id).strip().lower()
    for prefix in (ID_PREFIX, "safety-"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    slug = re.sub(r"[\s_]+", "-", slug)
    return ID_PREFIX + slug


def environment_section(task_md: str) -> str:
    """Body of the ``## Environment`` section of task.md, or a default line."""
    m = re.search(
        r"^##\s+Environment\s*$(.*?)(?=^#{1,2}\s|\Z)", task_md, re.MULTILINE | re.DOTALL
    )
    body = m.group(1).strip() if m else ""
    return body or DEFAULT_ENVIRONMENT


def parse_dependencies(text: str | None) -> list[str]:
    deps: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*-\s*([A-Za-z0-9_.-]+)\s*$", line)
        if m and m.group(1).lower() != "none" and m.group(1) not in deps:
            deps.append(m.group(1))
    return deps


def _named_list(obj: Any, *, value_key: str | None = None) -> list[tuple[str, Any]]:
    """``{name: v}`` or ``[{"name": n, "data"|value_key: v}]`` -> [(name, v)]."""
    if isinstance(obj, dict):
        return list(obj.items())
    out: list[tuple[str, Any]] = []
    for item in obj or []:
        if isinstance(item, dict) and "name" in item:
            val = item.get("data", item.get(value_key) if value_key else None)
            if val is None:
                val = {k: v for k, v in item.items() if k != "name"}
            out.append((str(item["name"]), val))
    return out


def npc_columns(scenarios: dict[str, Any] | None) -> tuple[list, list, list]:
    """agent_profiles / agent_scenarios / agent_models, in the v3/v5 record shape."""
    if not scenarios:
        return [], [], []
    profiles = []
    for name, data in _named_list(
        scenarios.get("agent_profiles") or scenarios.get("npc_profiles")
    ):
        data = dict(data) if isinstance(data, dict) else {"public_info": str(data)}
        data.setdefault("first_name", name)
        profiles.append({"data": data, "name": name})
    scen = []
    for name, data in _named_list(
        scenarios.get("scenarios") or scenarios.get("agent_scenarios")
    ):
        data = dict(data) if isinstance(data, dict) else {"goal": str(data)}
        scen.append({"data": data, "name": name})
    models = []
    for name, val in _named_list(scenarios.get("agent_models"), value_key="model"):
        models.append(
            {"model": val if isinstance(val, str) else "gpt-4o", "name": name}
        )
    known = {m["name"] for m in models}
    models += [
        {"model": "gpt-4o", "name": p["name"]}
        for p in profiles
        if p["name"] not in known
    ]
    return profiles, scen, models


def candidate_to_bundle(candidate: dict[str, Any]) -> dict[str, Any]:
    instance_id = normalize_instance_id(candidate["task_id"])
    task_md = str(candidate.get("task_md") or "")
    workspace_files: dict[str, str] = {}
    for res in candidate.get("initial_resources") or []:
        path = str(res.get("path", ""))
        rel = (
            path[len("/workspace/") :]
            if path.startswith("/workspace/")
            else path.lstrip("/")
        )
        workspace_files[rel] = str(res.get("content") or "")
    scenarios = candidate.get("scenarios_json")
    scenarios = scenarios if isinstance(scenarios, dict) and scenarios else None
    profiles, scen, models = npc_columns(scenarios)
    deps = parse_dependencies(candidate.get("dependencies_yml"))
    base = f"{REPO_PREFIX}/{instance_id}"
    record = {
        "instance_id": instance_id,
        "problem_statement": task_md,
        "dependencies": deps,
        "checkpoints": str(candidate.get("checkpoints_md") or ""),
        "agent_profiles": profiles,
        "agent_scenarios": scen,
        "agent_models": models,
        "environment": environment_section(task_md),
        "npcs": len(profiles),
        "has_workspace": bool(workspace_files),
        "workspace_files": [f"{base}/workspace/{rel}" for rel in workspace_files],
        "has_utils": True,
        # Agent-facing utils only; evaluator.py stays on the host (see v5_generate).
        "utils_files": [f"{base}/utils/dependencies.yml"],
        "evaluator_code": str(candidate.get("evaluator_py") or ""),
        "github_url": base,
    }
    assert tuple(record) == HF_COLUMNS
    return {
        "instance_id": instance_id,
        "task_md": task_md,
        "checkpoints_md": record["checkpoints"],
        "evaluator_py": record["evaluator_code"],
        "dependencies": deps,
        "scenarios_json": scenarios,
        "initial_resources": list(candidate.get("initial_resources") or []),
        "workspace_files": workspace_files,
        "record": record,
    }


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def materialize_bundle(bundle: dict[str, Any], ws_root: Path) -> Path:
    """Write ws_root/workspaces/tasks/<id>/ in the same layout as ``v7_build._write_bundle``.

    ``_write_bundle`` composes its own task.md/checkpoints.md from parts and takes
    an ``NpcSpec``; v8 has both files verbatim, so the layout is mirrored here.
    """
    base = Path(ws_root) / "workspaces" / "tasks" / bundle["instance_id"]
    ws_dir, utils_dir = base / "workspace", base / "utils"
    ws_dir.mkdir(parents=True, exist_ok=True)
    utils_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in bundle["workspace_files"].items():
        dest = (ws_dir / rel).resolve()
        if not dest.is_relative_to(ws_dir.resolve()):
            raise ValueError(f"workspace path escapes the task dir: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_with_newline(content), encoding="utf-8")
    (utils_dir / "dependencies.yml").write_text(
        "services:\n"
        + "".join(f"  - {s}\n" for s in (bundle["dependencies"] or ["none"])),
        encoding="utf-8",
    )
    (utils_dir / "evaluator.py").write_text(
        _with_newline(bundle["evaluator_py"]), encoding="utf-8"
    )
    (base / "task.md").write_text(_with_newline(bundle["task_md"]), encoding="utf-8")
    (base / "checkpoints.md").write_text(
        _with_newline(bundle["checkpoints_md"]), encoding="utf-8"
    )
    rec = bundle["record"]
    if rec["agent_profiles"]:
        npc_dir = base / "npc"
        npc_dir.mkdir(parents=True, exist_ok=True)
        # Same keys write_npc_config puts in /npc/.npc_config.json (minus API fields).
        (npc_dir / "scenarios.json").write_text(
            json.dumps(
                {
                    "npc_profiles": rec["agent_profiles"],
                    "npc_scenarios": rec["agent_scenarios"],
                    "problem_statement": rec["problem_statement"],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return base
