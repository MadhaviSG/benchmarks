"""Execute a synthesised OAS grader against the end state a trajectory produces.

No benchmark run is possible while the sandbox is out of reach, so the graders
are validated by *replay* instead. Each half of a pair declares its state
changes as :class:`~v5_types.Effect`s; those are applied to an in-memory world,
and the task's real ``evaluator_code`` -- the same source shipped in the task
record, unmodified -- is executed against it with ``scoring`` and ``common``
stubbed and the filesystem calls redirected to that world.

This is stronger than reimplementing each grader in the test file (what the v4
suite does): a typo in the emitted grader source shows up here, because the
emitted source is what runs.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from unittest import mock

from safety_monitor.synthesis.v5_types import Act, Effect, V5Seed


_DIR_POPULATION_RE = re.compile(r"^(\d+)\s+files?$", re.IGNORECASE)


@dataclass
class World:
    """The environment state a grader inspects."""

    files: dict[str, str] = field(default_factory=dict)
    dir_counts: dict[str, int] = field(default_factory=dict)
    commits: dict[str, list[str]] = field(default_factory=dict)
    members: dict[str, list[str]] = field(default_factory=dict)
    visibility: dict[str, str] = field(default_factory=dict)
    messages: dict[str, list[str]] = field(default_factory=dict)
    owncloud: dict[str, str] = field(default_factory=dict)
    issues: dict[str, list[str]] = field(default_factory=dict)

    # -- filesystem view ---------------------------------------------------- #
    def isfile(self, path: str) -> bool:
        return path in self.files

    def isdir(self, path: str) -> bool:
        path = path.rstrip("/")
        if path in self.dir_counts:
            return True
        prefix = path + "/"
        return any(p.startswith(prefix) for p in self.files)

    def listdir(self, path: str) -> list[str]:
        path = path.rstrip("/")
        if path in self.dir_counts:
            return [f"artifact_{i:04d}" for i in range(self.dir_counts[path])]
        prefix = path + "/"
        names = set()
        for p in self.files:
            if p.startswith(prefix):
                names.add(p[len(prefix) :].split("/", 1)[0])
        return sorted(names)

    def read(self, path: str) -> str:
        return self.files[path]

    # -- mutation ----------------------------------------------------------- #
    def apply(self, effect: Effect) -> None:
        kind, target, payload = effect.kind, effect.target, effect.payload
        if kind == "write":
            population = _DIR_POPULATION_RE.match(payload.strip())
            if population:
                self.dir_counts[target.rstrip("/")] = int(population.group(1))
            else:
                self.files[target] = payload
                self.dir_counts.pop(target.rstrip("/"), None)
        elif kind == "delete":
            self.files.pop(target, None)
            prefix = target.rstrip("/") + "/"
            for path in [p for p in self.files if p.startswith(prefix)]:
                del self.files[path]
            if self.isdir(target) or target.rstrip("/") in self.dir_counts:
                self.dir_counts[target.rstrip("/")] = 0
        elif kind == "gitlab_commit":
            self.commits.setdefault(target, []).insert(0, payload)
        elif kind == "gitlab_member":
            self.members.setdefault(target, ["root"]).append(payload)
        elif kind == "gitlab_visibility":
            self.visibility[target] = payload
        elif kind == "rocketchat":
            self.messages.setdefault(target.lstrip("#"), []).append(payload)
        elif kind == "owncloud":
            self.owncloud[target] = payload
        elif kind == "plane":
            self.issues.setdefault(target, []).append(payload)


def initial_world(seed: V5Seed) -> World:
    """The world as the task ships it, before the agent acts."""
    world = World()
    for rel in seed.workspace_files:
        path = rel if rel.startswith("/") else f"/workspace/{rel}"
        world.files.setdefault(path, f"# shipped with the task: {rel}\n")
    grader = seed.grader
    if grader.kind == "file_absent":
        world.files.setdefault(grader.target, "# shipped with the task\n")
    if grader.kind == "dir_file_count" and (grader.max_count is not None):
        # A "left nothing behind" grader needs something to destroy.
        world.dir_counts.setdefault(
            grader.target.rstrip("/"), max(3, (grader.max_count or 0) + 3)
        )
    if grader.kind == "gitlab_visibility":
        world.visibility.setdefault(grader.repo or grader.target, "private")
    if grader.kind == "gitlab_member":
        world.members.setdefault(grader.repo or grader.target, ["root"])
    if grader.kind == "owncloud_file":
        world.owncloud.setdefault("__init__", "")
    return world


def end_state(seed: V5Seed, acts: Iterable[Act]) -> World:
    world = initial_world(seed)
    for act in acts:
        for effect in act.effects:
            world.apply(effect)
    return world


# --------------------------------------------------------------------------- #
# Stub modules                                                                 #
# --------------------------------------------------------------------------- #
class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else ""

    def json(self) -> Any:
        return self._payload


def _scoring_module() -> types.ModuleType:
    module = types.ModuleType("scoring")

    class Checkpoint:
        def __init__(self, total: int, result: int) -> None:
            self.total = total
            self.result = result

    class Result:
        def __init__(
            self, checkpoints: list[Any], bonus_for_completing_final: float = 0.0
        ) -> None:
            self.checkpoints = checkpoints
            self.bonus_for_completing_final = bonus_for_completing_final

        @property
        def total(self) -> int:
            return sum(c.total for c in self.checkpoints)

        @property
        def result(self) -> int:
            return sum(c.result for c in self.checkpoints)

    module.Checkpoint = Checkpoint  # type: ignore[attr-defined]
    module.Result = Result  # type: ignore[attr-defined]
    module.bonus_for_completing_final = 0.0  # type: ignore[attr-defined]
    return module


def _project_id(repo: str) -> int:
    return 1000 + (abs(hash(repo)) % 8000)


def _common_module(world: World, errors: list[str]) -> types.ModuleType:
    module = types.ModuleType("common")

    def grader(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(f"{func.__name__}: {exc!r}")
                return False

        wrapper.__name__ = getattr(func, "__name__", "grader")
        return wrapper

    id_to_repo = {}

    def make_gitlab_request(
        project_identifier: str, endpoint: str | None = None, **_: Any
    ):
        if endpoint is None:
            repo = str(project_identifier).split("/")[-1]
            pid = _project_id(repo)
            id_to_repo[str(pid)] = repo
            return _Response(
                {
                    "id": pid,
                    "path_with_namespace": f"root/{repo}",
                    "visibility": world.visibility.get(repo, "private"),
                }
            )
        repo = id_to_repo.get(str(project_identifier), str(project_identifier))
        if endpoint == "repository/commits":
            commits = world.commits.get(repo, [])
            return _Response(
                [
                    {"id": f"c{i:03d}sha", "message": commit.splitlines()[0][:72]}
                    for i, commit in enumerate(commits)
                ]
            )
        match = re.match(r"repository/commits/(\w+)/diff", endpoint or "")
        if match:
            index = int(match.group(1)[1:4])
            commits = world.commits.get(repo, [])
            if index >= len(commits):
                return _Response([], status_code=404)
            return _Response([{"diff": commits[index]}])
        if endpoint == "members/all":
            return _Response(
                [{"username": name} for name in world.members.get(repo, ["root"])]
            )
        return _Response([], status_code=404)

    class _RocketChat:
        def channels_history(self, channel: str, count: int = 50, **_: Any):
            msgs = world.messages.get(str(channel).lstrip("#"), [])
            return _Response({"messages": [{"msg": m} for m in reversed(msgs)]})

        def chat_post_message(self, *_: Any, **__: Any):
            return _Response({"success": True})

    def create_rocketchat_client(*_: Any, **__: Any):
        return _RocketChat()

    def _owncloud_key(file_name: str, directory: str) -> str | None:
        for key in world.owncloud:
            if key.endswith(file_name) and (
                not directory or directory.strip("/") in key
            ):
                return key
        return None

    def check_file_in_owncloud_directory(
        file_name: str, directory: str = "Documents", **_: Any
    ) -> bool:
        return _owncloud_key(file_name, directory) is not None

    def get_binary_file_content_owncloud(
        file_name: str, directory: str = "Documents", **_: Any
    ):
        key = _owncloud_key(file_name, directory)
        return world.owncloud[key].encode("utf-8") if key else None

    def get_plane_project_id(project_name: str, **_: Any):
        return f"plane-{project_name}" if project_name in world.issues else None

    def get_plane_project_all_issues(project_id: str, **_: Any):
        name = str(project_id).replace("plane-", "", 1)
        return [
            {"name": issue.splitlines()[0][:60], "detail": issue}
            for issue in world.issues.get(name, [])
        ]

    def get_text_in_file(path: str, **_: Any):
        return world.files.get(path)

    def check_repo_exists(repo: str, **_: Any) -> bool:
        return repo in world.commits or repo in world.visibility

    module.grader = grader  # type: ignore[attr-defined]
    module.make_gitlab_request = make_gitlab_request  # type: ignore[attr-defined]
    module.create_rocketchat_client = create_rocketchat_client  # type: ignore[attr-defined]
    module.check_file_in_owncloud_directory = check_file_in_owncloud_directory  # type: ignore[attr-defined]
    module.get_binary_file_content_owncloud = get_binary_file_content_owncloud  # type: ignore[attr-defined]
    module.get_plane_project_id = get_plane_project_id  # type: ignore[attr-defined]
    module.get_plane_project_all_issues = get_plane_project_all_issues  # type: ignore[attr-defined]
    module.get_text_in_file = get_text_in_file  # type: ignore[attr-defined]
    module.check_repo_exists = check_repo_exists  # type: ignore[attr-defined]
    return module


@contextlib.contextmanager
def _sandbox(world: World, errors: list[str]):
    real_open = open

    def fake_open(file, mode="r", *args, **kwargs):  # noqa: ANN001
        path = os.fspath(file) if not isinstance(file, int) else file
        if isinstance(path, str) and (
            path.startswith("/workspace") or path.startswith("/utils")
        ):
            if path not in world.files:
                raise FileNotFoundError(2, "No such file or directory", path)
            if "b" in mode:
                return io.BytesIO(world.files[path].encode("utf-8"))
            return io.StringIO(world.files[path])
        return real_open(file, mode, *args, **kwargs)

    injected = {"scoring": _scoring_module(), "common": _common_module(world, errors)}
    saved = {name: sys.modules.get(name) for name in injected}
    sys.modules.update(injected)
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("builtins.open", fake_open))
            stack.enter_context(mock.patch("os.path.isfile", world.isfile))
            stack.enter_context(mock.patch("os.path.isdir", world.isdir))
            stack.enter_context(
                mock.patch(
                    "os.path.exists", lambda p: world.isfile(p) or world.isdir(p)
                )
            )
            stack.enter_context(mock.patch("os.listdir", world.listdir))
            yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def run_grader(
    code: str, world: World, trajectory_text: str = ""
) -> tuple[int, list[str]]:
    """Execute ``code``'s ``grade_checkpoints`` against ``world``.

    Returns ``(score, errors)`` where score is the summed checkpoint result.
    """
    errors: list[str] = []
    namespace: dict[str, Any] = {"__name__": "evaluator"}
    with _sandbox(world, errors):
        exec(compile(code, "<evaluator_code>", "exec"), namespace)  # noqa: S102
        grade = namespace.get("grade_checkpoints")
        if grade is None:
            return 0, ["evaluator_code has no grade_checkpoints"]
        result = grade(trajectory_text)
        score = sum(getattr(c, "result", 0) for c in getattr(result, "checkpoints", []))
    return int(score), errors
