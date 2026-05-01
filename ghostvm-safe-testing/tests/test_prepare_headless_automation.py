from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_prepare_headless_automation.sh"


class TestPrepareHeadlessAutomationScript(unittest.TestCase):
    def test_help_includes_offline_seed_flags(self) -> None:
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("offline guest-disk seeding", proc.stdout)
        self.assertIn("replaces it", proc.stdout)
        self.assertIn("--appleevent-target", proc.stdout)
        self.assertIn("--tcc-client", proc.stdout)
        self.assertIn("--tcc-service", proc.stdout)
        self.assertIn("--skip-safari-js-apple-events", proc.stdout)
        self.assertIn("--prime-local-network", proc.stdout)
        self.assertIn("--xcode-ui-testing", proc.stdout)
        self.assertIn("Xcode Helper.app", proc.stdout)
        self.assertIn("xcrun", proc.stdout)
        self.assertIn("automationmodetool", proc.stdout)
        self.assertIn("GHOSTVM_GUEST_SUDO_PASSWORD", proc.stdout)

    def test_offline_seed_happens_before_vm_start(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        i_seed = text.find("[prep] applying offline guest-disk seed")
        i_start = text.find("[prep] starting VM via GhostVMHelper (background)")
        self.assertNotEqual(i_seed, -1, "expected offline seed marker")
        self.assertNotEqual(i_start, -1, "expected VM start marker")
        self.assertLess(i_seed, i_start)

    def test_snapshot_replacement_deletes_before_create(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        i_delete = text.find("[prep] deleting existing snapshot:")
        i_create = text.find("[prep] creating snapshot:")
        self.assertNotEqual(i_delete, -1, "expected snapshot delete marker")
        self.assertNotEqual(i_create, -1, "expected snapshot create marker")
        self.assertLess(i_delete, i_create)

    def test_waits_for_ghosttools_health_after_socket(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("[prep] waiting for GhostTools /health", text)
        self.assertIn("GhostTools /health failed after waiting for guest login", text)

    def test_xcode_ui_testing_forces_bootstrap_before_snapshot(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        guard = "PRIME_AUTOMATION -eq 0 && $PRIME_LOCAL_NETWORK -eq 0 && $XCODE_UI_TESTING -eq 0"
        self.assertIn(guard, text)
        i_bootstrap = text.find("run_xcode_ui_testing_bootstrap")
        i_stop = text.find("[prep] stopping VM (snapshot requires stopped VM)")
        self.assertNotEqual(i_bootstrap, -1, "expected Xcode UI testing bootstrap")
        self.assertNotEqual(i_stop, -1, "expected snapshot stop marker")
        self.assertLess(i_bootstrap, i_stop)


if __name__ == "__main__":
    unittest.main()
