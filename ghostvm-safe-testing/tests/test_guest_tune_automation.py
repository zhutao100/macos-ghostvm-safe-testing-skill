from __future__ import annotations

import importlib.util
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

# Load the privacy seed module first because ghostvm_guest_tune_automation imports it by name.
SEED_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_guest_privacy_seed.py"
SEED_SPEC = importlib.util.spec_from_file_location("ghostvm_guest_privacy_seed", SEED_PATH)
assert SEED_SPEC and SEED_SPEC.loader
seed = importlib.util.module_from_spec(SEED_SPEC)
sys.modules[SEED_SPEC.name] = seed
SEED_SPEC.loader.exec_module(seed)

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_guest_tune_automation.py"
SPEC = importlib.util.spec_from_file_location("ghostvm_guest_tune_automation", MODULE_PATH)
assert SPEC and SPEC.loader
tune = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tune
SPEC.loader.exec_module(tune)


class TestGuestTuneAutomation(unittest.TestCase):
    def test_apply_tuning_writes_update_lock_time_machine_and_spotlight_settings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            (data_root / "Users" / "agent").mkdir(parents=True)
            (data_root / "private" / "var" / "root").mkdir(parents=True)

            rc = tune.apply_tuning(
                data_root,
                users=["agent"],
                skip_software_update=False,
                keep_security_responses=False,
                skip_screen_lock=False,
                skip_time_machine=False,
                skip_spotlight=False,
            )
            self.assertEqual(rc, 0)

            with (data_root / "Library" / "Preferences" / "com.apple.SoftwareUpdate.plist").open(
                "rb"
            ) as fh:
                software_update = plistlib.load(fh)
            for key in (
                "AutomaticCheckEnabled",
                "AutomaticDownload",
                "AutomaticallyInstallMacOSUpdates",
                "ConfigDataInstall",
                "CriticalUpdateInstall",
            ):
                self.assertIs(software_update[key], False)

            with (
                data_root
                / "Users"
                / "agent"
                / "Library"
                / "Preferences"
                / "com.apple.screensaver.plist"
            ).open("rb") as fh:
                screensaver = plistlib.load(fh)
            self.assertEqual(screensaver["askForPassword"], 0)
            self.assertEqual(screensaver["idleTime"], 0)

            with (data_root / "Library" / "Preferences" / "com.apple.TimeMachine.plist").open(
                "rb"
            ) as fh:
                time_machine = plistlib.load(fh)
            self.assertIs(time_machine["DoNotOfferNewDisksForBackup"], True)
            self.assertIs(time_machine["AutoBackup"], False)

            self.assertTrue((data_root / ".metadata_never_index").exists())

    def test_keep_security_responses_leaves_security_keys_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            tune.apply_tuning(
                data_root,
                users=None,
                skip_software_update=False,
                keep_security_responses=True,
                skip_screen_lock=True,
                skip_time_machine=True,
                skip_spotlight=True,
            )
            with (data_root / "Library" / "Preferences" / "com.apple.SoftwareUpdate.plist").open(
                "rb"
            ) as fh:
                software_update = plistlib.load(fh)
            self.assertNotIn("ConfigDataInstall", software_update)
            self.assertNotIn("CriticalUpdateInstall", software_update)


if __name__ == "__main__":
    unittest.main()
