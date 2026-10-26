from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ghostvm_guest_ready.sh"


class TestGuestPython(unittest.TestCase):
    def check_program(self) -> str:
        text = SCRIPT.read_text()
        match = re.search(r"python_check=\"\$\(\s*cat <<'PY'\n(.*?)\nPY", text, re.DOTALL)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_checks_functional_native_stdlib(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", self.check_program(), "3.10"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("XML/SQLite operations OK", proc.stdout)

    def test_old_version_is_a_failure(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", self.check_program(), "999.0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("select a modern guest interpreter", proc.stderr)

    def test_broken_expat_is_a_failure(self) -> None:
        program = "import sys; sys.modules['pyexpat'] = None\n" + self.check_program()
        proc = subprocess.run(
            [sys.executable, "-c", program, "3.10"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pyexpat", proc.stderr)

    def test_python_check_uses_argument_array(self) -> None:
        text = SCRIPT.read_text()
        self.assertIn('\'exec "$@"\' _ "$GUEST_PYTHON" -c "$python_check" "$REQUIRE_PYTHON"', text)

    def test_python_requires_success_and_functional_acknowledgement(self) -> None:
        text = SCRIPT.read_text()
        start = text.index("    if python_report=")
        end = text.index("\nfi\n\n# Xcode CLT", start)
        harness = (
            "set -euo pipefail\n"
            "EXEC_SH=fake_exec\nsock_path=unused\nGUEST_PYTHON=unused\n"
            "python_check=unused\nREQUIRE_PYTHON=3.13\nmissing=0\n"
            'fake_exec() { printf "%s" "$FAKE_REPORT"; return "$FAKE_STATUS"; }\n'
            "check_ok() { :; }\ncheck_warn() { missing=1; }\nsay() { :; }\n"
            + text[start:end]
            + '\nexit "$missing"\n'
        )
        acknowledgement = "Native stdlib imports and XML/SQLite operations OK"
        for report, status, expected in (
            (acknowledgement, 0, 0),
            ("", 0, 1),
            (acknowledgement, 1, 1),
        ):
            with self.subTest(report=report, status=status):
                proc = subprocess.run(
                    ["bash", "-c", harness],
                    env={**os.environ, "FAKE_REPORT": report, "FAKE_STATUS": str(status)},
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(proc.returncode, expected, proc.stderr)

    def test_rejects_invalid_minimum_before_contacting_vm(self) -> None:
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--vm", "unused", "--require-python", "3;bad"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("major.minor", proc.stderr)
