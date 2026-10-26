from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class StartupSocketTests(unittest.TestCase):
    def test_failed_lookup_output_is_not_retained_as_a_socket(self) -> None:
        text = (SCRIPTS / "ghostvm_safe_test.sh").read_text()
        start = text.index("# Wait for socket.")
        end = text.index('say "[runner] socket=$sock_path"', start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vmctl = root / "vmctl"
            vmctl.write_text("#!/bin/bash\nprintf 'Error: VM is not running\\n'\nexit 1\n")
            vmctl.chmod(0o755)
            log = root / "start.log"
            log.touch()
            harness = (
                "set -euo pipefail\n"
                'say() { printf "%s\\n" "$*" >&2; }\n'
                'action_required() { printf "%s\\n" "$*" >&2; exit 9; }\n'
                "START_PID=99999999\n"
                + text[start:end]
                + '\nprintf "incorrect socket=%s\\n" "$sock_path"\n'
            )
            proc = subprocess.run(
                ["bash", "-c", harness],
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "BUNDLE_PATH": str(root),
                    "START_LOG": str(log),
                },
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(proc.returncode, 9, proc.stderr)
            self.assertIn("Timed out waiting for Host API socket", proc.stderr)
            self.assertNotIn("incorrect socket", proc.stdout)

    def test_all_startup_waiters_clear_failed_lookup_output(self) -> None:
        for name in (
            "ghostvm_safe_test.sh",
            "ghostvm_doctor.sh",
            "ghostvm_prepare_headless_automation.sh",
        ):
            with self.subTest(name=name):
                text = (SCRIPTS / name).read_text()
                self.assertRegex(
                    text,
                    r'if sock_path=.*?; then\n\s+break\n\s+fi\n\s+sock_path=""',
                )
