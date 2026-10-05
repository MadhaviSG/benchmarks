"""File-absent graders ship with their target already in the workspace list."""

from safety_monitor.synthesis.v5_types import (
    Grader,
    workspace_files_including_absence_target,
)


def test_file_absent_target_is_added() -> None:
    grader = Grader(kind="file_absent", target="/workspace/protected/example.json")
    files = workspace_files_including_absence_target(
        ["controls/example.md", "briefing/example.md"], grader
    )
    assert files[-1] == "protected/example.json"
    assert workspace_files_including_absence_target(files, grader) == files


def test_other_grader_kinds_are_unchanged() -> None:
    grader = Grader(kind="file_contains", target="/workspace/reports/out.md")
    files = ["controls/example.md"]
    assert workspace_files_including_absence_target(files, grader) == files
