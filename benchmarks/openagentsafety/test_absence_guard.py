"""Rollouts refuse an absence grader whose target is not in the file list."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "absence_guard", Path(__file__).with_name("absence_guard.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
unstaged_absence_target = _MODULE.unstaged_absence_target

ABSENCE = """
TARGET = '/workspace/protected/example.json'

def grade_checkpoint1() -> bool:
    if os.path.isfile(TARGET):
        return False
    return True
"""


class AbsenceGuardTest(unittest.TestCase):
    def test_missing_target_is_refused(self) -> None:
        self.assertEqual(
            unstaged_absence_target(
                {
                    "evaluator_code": ABSENCE,
                    "workspace_files": [
                        "workspaces/tasks/example/workspace/controls/example.md"
                    ],
                }
            ),
            "/workspace/protected/example.json",
        )

    def test_listed_target_is_allowed(self) -> None:
        self.assertIsNone(
            unstaged_absence_target(
                {
                    "evaluator_code": ABSENCE,
                    "workspace_files": [
                        "workspaces/tasks/example/workspace/protected/example.json"
                    ],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
