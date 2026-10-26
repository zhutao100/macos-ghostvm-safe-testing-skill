from __future__ import annotations

import argparse
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
    def test_request_timeout_is_bounded_and_not_reported_as_cancellation(self) -> None:
        mod = load_module()
        with mock.patch.object(
            mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("vmctl", 2)
        ) as run:
            code, out, err = mod.execute(["vmctl"], 2)
        self.assertEqual((code, out), (124, ""))
        self.assertIn("may still be running", err)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_wait_ready_accepts_cold_boot_longer_than_thirty_seconds(self) -> None:
        mod = load_module()
        now = [0.0]

        def execute(cmd, timeout):
            self.assertEqual(cmd[-3:], ["exec", "/bin/echo", "ok"])
            self.assertLessEqual(timeout, 10)
            now[0] += timeout
            return (0, "ok", "") if now[0] > 30 else (1, "", "not ready")

        def sleep(seconds):
            now[0] += seconds

        with (
            mock.patch.object(mod.time, "monotonic", side_effect=lambda: now[0]),
            mock.patch.object(mod.time, "sleep", side_effect=sleep),
            mock.patch.object(mod, "execute", side_effect=execute),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(mod.wait_ready(["vmctl"], 120), 0)
        self.assertGreater(now[0], 30)

    def test_wait_ready_deadline_preserves_last_error(self) -> None:
        mod = load_module()
        now = [0.0]

        def execute(cmd, timeout):
            now[0] += timeout
            return 13, "", "GhostTools not initialized"

        stderr = io.StringIO()
        with (
            mock.patch.object(mod.time, "monotonic", side_effect=lambda: now[0]),
            mock.patch.object(mod, "execute", side_effect=execute),
            mock.patch.object(mod.time, "sleep"),
            redirect_stderr(stderr),
        ):
            self.assertEqual(mod.wait_ready(["vmctl"], 0.5), 124)
        self.assertEqual(now[0], 0.5)
        self.assertIn("GhostTools not initialized", stderr.getvalue())

    def test_readiness_cannot_replay_a_supplied_command(self) -> None:
        mod = load_module()
        with (
            mock.patch.object(
                sys,
                "argv",
                ["ghostvm_remote_exec.py", "--name", "dev", "--wait-ready", "1", "/bin/false"],
            ),
            mock.patch.object(mod.subprocess, "run") as run,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            mod.main()
        run.assert_not_called()

    def test_zero_status_without_probe_acknowledgement_is_not_ready(self) -> None:
        mod = load_module()
        now = [0.0]

        def execute(cmd, timeout):
            now[0] += timeout
            return 0, "", ""

        with (
            mock.patch.object(mod.time, "monotonic", side_effect=lambda: now[0]),
            mock.patch.object(mod, "execute", side_effect=execute),
            mock.patch.object(mod.time, "sleep"),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(mod.wait_ready(["vmctl"], 0.5), 124)

    def test_rejects_unbounded_or_nonpositive_timeouts(self) -> None:
        mod = load_module()
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                mod.positive_seconds(value)

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

    def test_invalid_guest_result_is_not_success(self) -> None:
        for payload in (
            "{}",
            "[]",
            '{"exitCode":null}',
            '{"exitCode":true}',
            '{"exitCode":"0"}',
            '{"exitCode":0,"stdout":[]}',
        ):
            with self.subTest(payload=payload):
                mod = load_module()
                cp = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
                with (
                    mock.patch.object(mod.subprocess, "run", return_value=cp),
                    mock.patch.object(
                        sys,
                        "argv",
                        ["ghostvm_remote_exec.py", "--name", "dev", "/bin/true"],
                    ),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(mod.main(), 2)


if __name__ == "__main__":
    unittest.main()
