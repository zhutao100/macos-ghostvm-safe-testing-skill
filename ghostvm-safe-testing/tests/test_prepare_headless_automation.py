from __future__ import annotations

import os
import subprocess
import tempfile
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
        self.assertIn("--replace-snapshot", proc.stdout)
        self.assertIn("fails without deleting it", proc.stdout)
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

    def test_snapshot_replacement_requires_explicit_flag(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        i_guard = text.find("REPLACE_SNAPSHOT -eq 1")
        i_delete = text.find("[prep] deleting existing snapshot:")
        i_create = text.find("[prep] creating snapshot:")
        self.assertNotEqual(i_guard, -1, "expected replacement flag guard")
        self.assertNotEqual(i_delete, -1, "expected snapshot delete marker")
        self.assertNotEqual(i_create, -1, "expected snapshot create marker")
        self.assertLess(i_guard, i_delete)
        self.assertLess(i_delete, i_create)
        self.assertIn("already exists. Choose a new --snapshot name", text)

    def test_target_snapshot_preflight_happens_before_stopping_vm(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        i_preflight = text.find('preflight_target_snapshot "$SNAPSHOT_NAME"')
        i_stop = text.find("stop_vm_if_running", i_preflight)
        self.assertNotEqual(i_preflight, -1, "expected target snapshot preflight")
        self.assertNotEqual(i_stop, -1, "expected VM stop after preflight")
        self.assertLess(i_preflight, i_stop)

    def test_existing_snapshot_fails_before_stop_without_replace_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()

            calls = root / "vmctl.calls"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            vmctl = bin_dir / "vmctl"
            vmctl.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'printf "%s\\n" "$*" >>"${GHOSTVM_TEST_CALLS:?}"\n'
                "if [[ ${1:-} == snapshot && ${3:-} == list ]]; then\n"
                "  printf '%s\\n' automation-ready\n"
                "  exit 0\n"
                "fi\n"
                "if [[ ${1:-} == socket ]]; then\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            vmctl.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
            env["GHOSTVM_TEST_CALLS"] = str(calls)

            proc = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--bundle",
                    str(bundle),
                    "--snapshot",
                    "automation-ready",
                ],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 1)
            self.assertIn("already exists", proc.stderr)

            recorded = calls.read_text(encoding="utf-8")
            self.assertIn("snapshot", recorded)
            self.assertIn("list", recorded)
            self.assertNotIn("delete", recorded)
            self.assertNotIn("stop", recorded)

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
