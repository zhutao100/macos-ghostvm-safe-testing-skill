from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ghostvm_safe_test.sh"


class GuestJobTests(unittest.TestCase):
    def run_job(self, result: str, *, mode: str = "complete", timeout: int = 5):
        text = SCRIPT.read_text()
        start = text.index("# Keep the REST request short;")
        end = text.index('say "[runner] guest command OK"', start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_exec = root / "ghostvm_exec.sh"
            fake_exec.write_text(
                "#!/bin/bash\n"
                'printf "%s\\n" "$@" >"$HOST_RUN_DIR/launch-args"\n'
                '[[ "$FAKE_MODE" == launch-fail ]] && exit 17\n'
                '[[ "$FAKE_MODE" == never ]] && exit 0\n'
                'printf "%s\\n" "$FAKE_RESULT" >"$HOST_RUN_DIR/exit_code"\n'
            )
            fake_exec.chmod(0o755)
            runner = root / "run.sh"
            runner.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                'say() { printf "%s\\n" "$*" >&2; }\n'
                "KEEP_RUNNING=1\n"
                'trap \'printf "keep_running=%s\\n" "$KEEP_RUNNING" >&2\' EXIT\n'
                "sock_path=/tmp/unused.sock\n"
                "GUEST_RUN_SCRIPT_GUEST=/guest/job.sh\n"
                "GUEST_RW=/guest-output\nVM_NAME=sample\nRUN_ID=sample\n" + text[start:end]
            )
            process = subprocess.run(
                ["bash", str(runner)],
                env={
                    **os.environ,
                    "HOST_RUN_DIR": str(root),
                    "CMD_TIMEOUT": str(timeout),
                    "FAKE_MODE": mode,
                    "FAKE_RESULT": result,
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            launch_args = (root / "launch-args").read_text().splitlines()
            self.assertEqual(launch_args[2:4], ["--timeout", "10"])
            return process

    def test_completion_artifact_propagates_guest_status(self) -> None:
        for value in (0, 37):
            with self.subTest(value=value):
                process = self.run_job(str(value))
                self.assertEqual(process.returncode, value, process.stderr)

    def test_invalid_completion_stops_kept_running_job(self) -> None:
        for value in ("", "false", "256", "0\n37"):
            with self.subTest(value=value):
                process = self.run_job(value)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn("keep_running=0", process.stderr)

    def test_launch_failure_is_not_a_successful_guest_result(self) -> None:
        process = self.run_job("0", mode="launch-fail")
        self.assertEqual(process.returncode, 17)

    def test_deadline_disables_keep_running_for_cleanup(self) -> None:
        process = self.run_job("0", mode="never", timeout=0)
        self.assertEqual(process.returncode, 124)
        self.assertIn("keep_running=0", process.stderr)
