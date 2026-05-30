from __future__ import annotations

import re
import unittest
from pathlib import Path

SAFE_TEST_SH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_safe_test.sh"


class TestSafeTestOrdering(unittest.TestCase):
    def test_snapshot_revert_happens_before_config_edit(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        i_revert = text.find("[runner] reverting snapshot")
        i_guard = text.find("[runner] applying temporary automation guards")
        i_config = text.find("[runner] configuring shared folders")
        self.assertNotEqual(i_revert, -1, "expected revert log marker")
        self.assertNotEqual(i_guard, -1, "expected guard log marker")
        self.assertNotEqual(i_config, -1, "expected configure log marker")
        self.assertLess(
            i_revert,
            i_guard,
            "safe runner must save guard state after snapshot revert",
        )
        self.assertLess(
            i_guard,
            i_config,
            "safe runner must save guard state before editing run-specific shares",
        )

    def test_preserves_exit_code_from_host_api_exec(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        self.assertNotIn('if ! "$EXEC_SH"', text)
        self.assertIn('if "$EXEC_SH"', text)

    def test_guest_run_script_captures_exit_code_on_failure(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        m = re.search(
            r'cat >"\$GUEST_RUN_SCRIPT_HOST" <<GUESTSH\n(.*?)\nGUESTSH\n',
            text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "expected guest_run.sh heredoc")
        guest = m.group(1)

        # Guard against `set -e` causing early exit before exporting artifacts.
        i_cmd = guest.find('/bin/zsh -lc "\\$CMD"')
        self.assertNotEqual(i_cmd, -1, "expected user command invocation in guest script")
        self.assertIn("set +e", guest[:i_cmd])
        self.assertIn("EXIT_CODE=\\$?", guest[i_cmd : i_cmd + 200])
        self.assertIn('print "\\$EXIT_CODE" >"\\$RUN_DIR/exit_code"', guest)

    def test_safe_runner_detects_virtiofs_mountpoint(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        self.assertIn("AppleVirtIOFS", text)

    def test_safe_runner_waits_for_health_after_socket(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        self.assertIn("[runner] waiting for GhostTools /health", text)
        self.assertIn("GhostTools /health failed after waiting for guest login", text)

    def test_restore_failure_is_not_silently_marked_restored(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        self.assertIn('if python3 "$GUARD_PY" restore --state "$AUTOMATION_STATE"', text)
        self.assertIn('say "[runner] error: failed to restore automation state', text)
        self.assertNotIn("warning: failed to restore automation state", text)


if __name__ == "__main__":
    unittest.main()
