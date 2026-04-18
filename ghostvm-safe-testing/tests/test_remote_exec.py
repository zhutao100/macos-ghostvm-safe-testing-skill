from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_remote_exec.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ghostvm_remote_exec", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRemoteExec(unittest.TestCase):
    def test_builds_vmctl_command_with_socket(self) -> None:
        mod = load_module()

        cp = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"stdout":"ok\\n","stderr":"","exitCode":0}',
            stderr="",
        )

        with mock.patch.object(mod.subprocess, "run", return_value=cp) as run:
            with mock.patch.object(
                sys,
                "argv",
                [
                    "ghostvm_remote_exec.py",
                    "--socket",
                    "/tmp/dev.sock",
                    "/usr/bin/uname",
                    "-a",
                ],
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = mod.main()

        self.assertEqual(rc, 0)
        run.assert_called_once()
        called = run.call_args.args[0]
        self.assertEqual(
            called,
            [
                "vmctl",
                "remote",
                "--json",
                "--socket",
                "/tmp/dev.sock",
                "exec",
                "/usr/bin/uname",
                "-a",
            ],
        )

    def test_builds_vmctl_command_with_name(self) -> None:
        mod = load_module()

        cp = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"stdout":"","stderr":"","exitCode":7}',
            stderr="",
        )

        with mock.patch.object(mod.subprocess, "run", return_value=cp) as run:
            with mock.patch.object(
                sys,
                "argv",
                [
                    "ghostvm_remote_exec.py",
                    "--name",
                    "dev",
                    "/bin/true",
                ],
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = mod.main()

        self.assertEqual(rc, 7)
        called = run.call_args.args[0]
        self.assertEqual(
            called,
            [
                "vmctl",
                "remote",
                "--json",
                "--name",
                "dev",
                "exec",
                "/bin/true",
            ],
        )

    def test_vmctl_failure_returns_vmctl_code(self) -> None:
        mod = load_module()

        cp = subprocess.CompletedProcess(
            args=[],
            returncode=42,
            stdout="",
            stderr="vmctl failed",
        )

        with mock.patch.object(mod.subprocess, "run", return_value=cp):
            with mock.patch.object(
                sys, "argv", ["ghostvm_remote_exec.py", "--name", "dev", "/bin/ls"]
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = mod.main()

        self.assertEqual(rc, 42)


if __name__ == "__main__":
    unittest.main()
