from __future__ import annotations

import argparse
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GIB = 1024**3


def load_module():
    with mock.patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
        spec = importlib.util.spec_from_file_location(
            "ghostvm_guest_os_check", SCRIPTS / "ghostvm_guest_os_check.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class TestGuestOSCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.output = (
            "ProductName: macOS\nProductVersion: 26.7.2\nBuildVersion: 25G332\n"
            f"hw.memsize: {16 * GIB}\nhw.memsize_usable: {15 * GIB}\n"
        )

    def assess(self, output=None):
        return self.module.assess(
            self.output if output is None else output,
            expected_version="26.7.2",
            expected_build="25G332",
            configured_memory=16 * GIB,
            minimum_fraction=0.8,
        )

    def test_exact_version_build_and_healthy_memory_pass(self) -> None:
        result = self.assess()
        self.assertTrue(result["passed"])
        self.assertEqual(result["usable_memory_bytes"], 15 * GIB)

    def test_regressed_usable_memory_fails_even_with_target_os(self) -> None:
        result = self.assess(self.output.replace(str(15 * GIB), "1879031808"))
        self.assertFalse(result["passed"])
        self.assertIn("below 80%", result["errors"][0])

    def test_cached_or_old_os_is_not_success(self) -> None:
        result = self.assess(self.output.replace("26.7.2", "26.2").replace("25G332", "25C56"))
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["errors"]), 2)

    def test_missing_usable_key_is_explicitly_unverified(self) -> None:
        output = "\n".join(
            line for line in self.output.splitlines() if not line.startswith("hw.memsize_usable")
        )
        result = self.assess(output)
        self.assertTrue(result["passed"])
        self.assertIsNone(result["usable_memory_bytes"])
        self.assertIn("not established", result["warnings"][0])

    def test_invalid_or_inconsistent_memory_fails(self) -> None:
        for key, value in (
            ("hw.memsize", "123"),
            ("hw.memsize", "invalid"),
            ("hw.memsize_usable", "-1"),
            ("hw.memsize_usable", str(17 * GIB)),
        ):
            with self.subTest(key=key, value=value):
                lines = [
                    f"{key}: {value}" if line.startswith(f"{key}:") else line
                    for line in self.output.splitlines()
                ]
                self.assertFalse(self.assess("\n".join(lines))["passed"])
        self.assertFalse(self.assess("")["passed"])

    def test_validates_cli_policy_values(self) -> None:
        for value in ("0", "-1", "nan", "inf", "1.1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                self.module.memory_fraction(value)
        for value in ("26", "latest", "26.7;false"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                self.module.version(value)

    def test_main_uses_guest_probe_and_propagates_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "Test.GhostVM"
            bundle.mkdir()
            (bundle / "config.json").write_text(json.dumps({"memoryBytes": 16 * GIB}))
            argv = ["check", "--bundle", str(bundle), "--expect-version", "26.7.2"]
            for code, output, expected in ((0, self.output, 0), (37, "", 1)):
                with (
                    self.subTest(code=code),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(
                        self.module.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0, "/tmp/test.sock\n", ""),
                    ),
                    mock.patch.object(
                        self.module, "execute", return_value=(code, output, "guest error")
                    ) as execute,
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(self.module.main(), expected)
                    command = execute.call_args.args[0]
                    self.assertEqual(command[-3:], ["/bin/sh", "-c", self.module.PROBE])
                    self.assertNotIn("lastInstallVersion", self.module.PROBE)

    def test_invalid_config_cannot_reach_guest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            for payload in ([], {}, {"memoryBytes": True}, {"memoryBytes": -1}):
                config.write_text(json.dumps(payload))
                with (
                    self.subTest(payload=payload),
                    mock.patch.object(sys, "argv", ["check", "--bundle", directory]),
                    mock.patch.object(self.module, "execute") as execute,
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(self.module.main(), 1)
                    execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
