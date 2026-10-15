"""Terminal-timeout cap and non-interactive sandbox env (no Docker)."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_SPEC = importlib.util.spec_from_file_location(
    "sandbox_hardening", Path(__file__).with_name("sandbox_hardening.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
cap = _MOD.cap_terminal_timeout


class CapTerminalTimeoutTest(unittest.TestCase):
    def test_milliseconds_are_converted(self) -> None:
        self.assertEqual(cap(120000), 120.0)

    def test_upper_clamp(self) -> None:
        self.assertEqual(cap(900), 600.0)
        self.assertEqual(cap(3600), 600.0)
        self.assertEqual(cap(float("inf")), 600.0)

    def test_lower_clamp(self) -> None:
        self.assertEqual(cap(0.2), 1.0)
        self.assertEqual(cap(0), 1.0)

    def test_none_and_garbage_return_default(self) -> None:
        self.assertIsNone(cap(None))
        self.assertEqual(cap(None, default=60.0), 60.0)
        self.assertEqual(cap("abc", default=60.0), 60.0)
        self.assertEqual(cap(float("nan"), default=60.0), 60.0)

    def test_in_range_passes_through(self) -> None:
        self.assertEqual(cap(30), 30.0)
        self.assertEqual(cap("45"), 45.0)


class NonInteractiveEnvTest(unittest.TestCase):
    def test_command_sets_pagers_and_prompt(self) -> None:
        cmd = _MOD.noninteractive_env_command()
        for needle in ("PAGER=cat", "GIT_PAGER=cat", "GIT_TERMINAL_PROMPT=0"):
            self.assertIn(needle, cmd)
        self.assertIn("/etc/bash.bashrc", cmd)
        self.assertIn("~/.bashrc", cmd)
        self.assertIn("/etc/environment", cmd)
        self.assertIn("git config --system core.pager cat", cmd)

    def test_command_is_valid_bash(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", "-c", _MOD.noninteractive_env_command()],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TerminalToolPatchTest(unittest.TestCase):
    """The live path: the agent server parses tool calls via action_from_arguments."""

    def setUp(self) -> None:
        try:
            from openhands.tools.terminal.definition import (
                TerminalAction,
                TerminalTool,
            )
        except ImportError:
            self.skipTest("openhands-tools not installed")
        self.assertTrue(_MOD.install_terminal_timeout_cap())
        self.assertTrue(_MOD.install_terminal_timeout_cap())  # idempotent
        self.tool_cls: Any = TerminalTool
        self.fake_tool = SimpleNamespace(action_type=TerminalAction)

    def _timeout(self, args: dict) -> float | None:
        return self.tool_cls.action_from_arguments(self.fake_tool, args).timeout

    def test_parsed_action_is_capped(self) -> None:
        self.assertEqual(
            self._timeout({"command": "git log", "timeout": 120000}), 120.0
        )
        self.assertEqual(self._timeout({"command": "x", "timeout": 900}), 600.0)
        self.assertEqual(self._timeout({"command": "x", "timeout": 0.2}), 1.0)
        self.assertIsNone(self._timeout({"command": "x"}))


class HardenSandboxTest(unittest.TestCase):
    """run_infer.harden_sandbox issues the upload and env commands."""

    def test_setup_steps(self) -> None:
        try:
            from benchmarks.openagentsafety import run_infer
        except ImportError:
            self.skipTest("benchmarks package not importable")

        commands: list[str] = []
        uploads: list[str] = []

        class FakeWorkspace:
            def execute_command(self, command: str, timeout: float = 0):
                commands.append(command)
                out = "/usr/lib/py/site-packages" if "sysconfig" in command else ""
                if "cap_terminal_timeout" in command:
                    out = "120.0"
                return SimpleNamespace(stdout=out, stderr="", exit_code=0)

            def file_upload(self, source_path: str, destination_path: str):
                uploads.append(destination_path)
                return SimpleNamespace(success=True)

        run_infer.harden_sandbox(FakeWorkspace())
        self.assertEqual(
            uploads,
            [
                "/usr/lib/py/site-packages/benchmarks/openagentsafety/"
                "sandbox_hardening.py"
            ],
        )
        env_cmds = [c for c in commands if "GIT_PAGER=cat" in c]
        self.assertEqual(len(env_cmds), 1)
        self.assertIn("PAGER=cat", env_cmds[0])

        from openhands.sdk.tool.registry import get_tool_module_qualnames

        self.assertEqual(
            get_tool_module_qualnames().get(_MOD.MARKER_TOOL_NAME),
            _MOD.MODULE_QUALNAME,
        )


if __name__ == "__main__":
    unittest.main()
