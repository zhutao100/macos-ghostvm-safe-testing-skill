from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_automation_guard.py"
SPEC = importlib.util.spec_from_file_location("ghostvm_automation_guard", MODULE_PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


class TestAutomationGuard(unittest.TestCase):
    def test_stable_hash_matches_ghostvm_examples(self) -> None:
        self.assertEqual(guard.stable_hash(""), 5381)
        self.assertEqual(guard.stable_hash("hello"), 210714636441)
        self.assertEqual(guard.stable_hash("/path/to/vm"), 13789981918659725477)
        self.assertEqual(guard.stable_hash("GhostVM"), 229425741865133)

    def test_patch_config_forces_nat_clears_forwards_and_legacy(self) -> None:
        cfg = {
            "sharedFolders": [{"id": "old", "path": "/missing", "readOnly": False}],
            "sharedFolderPath": "/missing",
            "sharedFolderReadOnly": True,
            "portForwards": [{"hostPort": 8000, "guestPort": 80, "enabled": True}],
            "networkConfig": {"mode": "bridged", "bridgeInterfaceIdentifier": "en0"},
        }

        patched = guard.patch_config(
            cfg,
            force_nat=True,
            disable_port_forwards=True,
            clear_legacy=True,
            clear_shared_folders=True,
        )

        self.assertEqual(
            patched["networkConfig"], {"mode": "nat", "bridgeInterfaceIdentifier": None}
        )
        self.assertEqual(patched["portForwards"], [])
        self.assertEqual(patched["sharedFolders"], [])
        self.assertIsNone(patched["sharedFolderPath"])
        self.assertFalse(patched["sharedFolderReadOnly"])

    def test_inspect_detects_missing_share_and_bad_bridged_config(self) -> None:
        cfg = {
            "sharedFolderPath": "/definitely/missing",
            "networkConfig": {"mode": "bridged", "bridgeInterfaceIdentifier": ""},
        }
        errors, warnings = guard.inspect_config(cfg)
        self.assertGreaterEqual(len(errors), 2)
        self.assertEqual(warnings, [])

    def test_duplicate_shared_folder_leaf_names_warn_but_do_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "one" / "share"
            second = root / "two" / "share"
            first.mkdir(parents=True)
            second.mkdir(parents=True)

            errors, warnings = guard.inspect_config(
                {
                    "sharedFolders": [
                        {"path": str(first), "readOnly": True},
                        {"path": str(second), "readOnly": False},
                    ]
                }
            )

        self.assertEqual(errors, [])
        self.assertIn("disambiguates duplicate guest mount names", "\n".join(warnings))

    def test_inspect_detects_unavailable_bridged_interface(self) -> None:
        original = guard.host_network_interface_ids
        guard.host_network_interface_ids = lambda: {"en0"}
        try:
            errors, _warnings = guard.inspect_config(
                {"networkConfig": {"mode": "bridged", "bridgeInterfaceIdentifier": "en9"}}
            )
        finally:
            guard.host_network_interface_ids = original

        self.assertIn("not available on this host: en9", "\n".join(errors))

    def test_apply_patches_fixable_config_issues_before_failing_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()
            cfg_path = bundle / "config.json"
            original = {
                "sharedFolderPath": "/definitely/missing",
                "sharedFolderReadOnly": True,
                "networkConfig": {"mode": "bridged", "bridgeInterfaceIdentifier": ""},
            }
            cfg_path.write_text(json.dumps(original) + "\n", encoding="utf-8")
            state = root / "state.json"

            apply_ns = SimpleNamespace(
                bundle=str(bundle),
                state=str(state),
                force=False,
                force_nat=True,
                disable_port_forwards=False,
                clear_shared_folders=False,
                configure_helper_defaults=False,
                keep_legacy_shared_folder=False,
                allow_existing_config_issues=False,
            )

            self.assertEqual(guard.apply_guard(apply_ns), 0)
            patched = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(
                patched["networkConfig"], {"mode": "nat", "bridgeInterfaceIdentifier": None}
            )
            self.assertIsNone(patched["sharedFolderPath"])
            self.assertFalse(patched["sharedFolderReadOnly"])

    def test_apply_and_restore_config_round_trip_without_host_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()
            share = root / "share"
            share.mkdir()
            cfg_path = bundle / "config.json"
            original = {
                "sharedFolders": [{"id": "1", "path": str(share), "readOnly": False}],
                "sharedFolderPath": "/legacy",
                "sharedFolderReadOnly": True,
                "portForwards": [{"id": "pf", "hostPort": 8000, "guestPort": 80, "enabled": True}],
                "networkConfig": {"mode": "bridged", "bridgeInterfaceIdentifier": "en0"},
            }
            cfg_path.write_text(json.dumps(original) + "\n", encoding="utf-8")
            state = root / "state.json"

            apply_ns = SimpleNamespace(
                bundle=str(bundle),
                state=str(state),
                force=False,
                force_nat=True,
                disable_port_forwards=True,
                clear_shared_folders=False,
                configure_helper_defaults=False,
                keep_legacy_shared_folder=False,
                allow_existing_config_issues=True,
            )

            self.assertEqual(guard.apply_guard(apply_ns), 0)
            patched = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(patched["networkConfig"]["mode"], "nat")
            self.assertEqual(patched["portForwards"], [])
            self.assertIsNone(patched["sharedFolderPath"])

            restore_ns = SimpleNamespace(
                state=str(state),
                stop_vm=False,
                stop_timeout=1,
                no_config=False,
                no_helper_defaults=False,
                delete_state=False,
            )

            self.assertEqual(guard.restore_guard(restore_ns), 0)
            restored = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
