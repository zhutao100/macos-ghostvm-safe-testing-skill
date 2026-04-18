from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_configure_shares.py"


class TestConfigureShares(unittest.TestCase):
    def _write_json(self, path: Path, obj: dict) -> None:
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    def test_updates_config_shared_folders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()

            ro = root / "ro-src"
            rw = root / "rw-out"
            ro.mkdir()
            rw.mkdir()

            cfg_path = bundle / "config.json"
            self._write_json(
                cfg_path,
                {
                    "cpus": 4,
                    "memoryBytes": 8 * (1 << 30),
                    "sharedFolderPath": "/legacy",
                    "sharedFolderReadOnly": True,
                },
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--bundle",
                    str(bundle),
                    "--ro",
                    str(ro),
                    "--rw",
                    str(rw),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            updated = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertIn("sharedFolders", updated)
            self.assertEqual(len(updated["sharedFolders"]), 2)

            ro_entry, rw_entry = updated["sharedFolders"]
            self.assertEqual(ro_entry["path"], str(ro.resolve()))
            self.assertTrue(ro_entry["readOnly"])
            self.assertEqual(rw_entry["path"], str(rw.resolve()))
            self.assertFalse(rw_entry["readOnly"])

            # Legacy keys preserved.
            self.assertEqual(updated["sharedFolderPath"], "/legacy")
            self.assertTrue(updated["sharedFolderReadOnly"])

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()

            ro = root / "ro-src"
            rw = root / "rw-out"
            ro.mkdir()
            rw.mkdir()

            cfg_path = bundle / "config.json"
            original = {"hello": "world"}
            self._write_json(cfg_path, original)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--bundle",
                    str(bundle),
                    "--ro",
                    str(ro),
                    "--rw",
                    str(rw),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            # File unchanged.
            self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8")), original)

            # Dry-run output contains sharedFolders.
            out = json.loads(proc.stdout)
            self.assertIn("sharedFolders", out)
            self.assertEqual(len(out["sharedFolders"]), 2)

    def test_rejects_same_leaf_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()

            (bundle / "config.json").write_text("{}\n", encoding="utf-8")

            # Same basename "input" in different parents.
            ro = root / "a" / "input"
            rw = root / "b" / "input"
            ro.mkdir(parents=True)
            rw.mkdir(parents=True)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--bundle",
                    str(bundle),
                    "--ro",
                    str(ro),
                    "--rw",
                    str(rw),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("different leaf directory names", (proc.stderr + proc.stdout))


if __name__ == "__main__":
    unittest.main()
