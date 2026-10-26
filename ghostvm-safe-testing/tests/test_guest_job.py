from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ghostvm_safe_test.sh"


class GuestJobTests(unittest.TestCase):
    def run_job(
        self, result: str, *, mode: str = "complete", timeout: int = 5, launch_grace: int = 30
    ):
        text = SCRIPT.read_text()
        start = text.index("# Keep the REST request short;")
        end = text.index('say "[runner] guest command OK"', start)
        job_block = text[start:end].replace(
            "launch_deadline=$((SECONDS + 30))",
            f"launch_deadline=$((SECONDS + {launch_grace}))",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_exec = root / "ghostvm_exec.sh"
            fake_exec.write_text(
                "#!/bin/bash\n"
                'printf "launch\\n" >>"$HOST_RUN_DIR/launch-count"\n'
                'printf "%s\\n" "$@" >"$HOST_RUN_DIR/launch-args"\n'
                '[[ "$FAKE_MODE" == launch-fail ]] && exit 17\n'
                '[[ "$FAKE_MODE" == never ]] && exit 0\n'
                'if [[ "$FAKE_MODE" == lost-ack-delayed ]]; then\n'
                '  printf "1\\n" >"$HOST_RUN_DIR/job_started"\n'
                '  (sleep 0.2; printf "%s\\n" "$FAKE_RESULT" >"$HOST_RUN_DIR/exit_code") >/dev/null 2>&1 </dev/null &\n'
                "  exit 28\n"
                "fi\n"
                'printf "%s\\n" "$FAKE_RESULT" >"$HOST_RUN_DIR/exit_code"\n'
                '[[ "$FAKE_MODE" == lost-ack ]] && exit 28\n'
                "exit 0\n"
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
                "GUEST_RW=/guest-output\nVM_NAME=sample\nRUN_ID=sample\n" + job_block
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
            self.assertEqual((root / "launch-count").read_text(), "launch\n")
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
        process = self.run_job("0", mode="launch-fail", timeout=1)
        self.assertEqual(process.returncode, 124)
        self.assertIn("keep_running=0", process.stderr)

    def test_lost_launch_acknowledgement_uses_guest_completion(self) -> None:
        for mode in ("lost-ack", "lost-ack-delayed"):
            for value in (0, 37):
                with self.subTest(value=value, mode=mode):
                    process = self.run_job(str(value), mode=mode)
                    self.assertEqual(process.returncode, value, process.stderr)
                    self.assertIn("without relaunching", process.stderr)

    def test_unconfirmed_launch_stops_vm_before_job_deadline(self) -> None:
        process = self.run_job("0", mode="launch-fail", launch_grace=0)
        self.assertEqual(process.returncode, 17)
        self.assertIn("no guest start/completion artifact", process.stderr)
        self.assertIn("keep_running=0", process.stderr)

    def test_deadline_disables_keep_running_for_cleanup(self) -> None:
        process = self.run_job("0", mode="never", timeout=0)
        self.assertEqual(process.returncode, 124)
        self.assertIn("keep_running=0", process.stderr)
