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
        self.assertIn("--appleevent-target", proc.stdout)
        self.assertIn("--tcc-client", proc.stdout)
        self.assertIn("--prime-local-network", proc.stdout)

    def test_offline_seed_happens_before_vm_start(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        i_seed = text.find("[prep] applying offline guest-disk seed")
        i_start = text.find("[prep] starting VM via GhostVMHelper (background)")
        self.assertNotEqual(i_seed, -1, "expected offline seed marker")
        self.assertNotEqual(i_start, -1, "expected VM start marker")
        self.assertLess(i_seed, i_start)


if __name__ == "__main__":
    unittest.main()
