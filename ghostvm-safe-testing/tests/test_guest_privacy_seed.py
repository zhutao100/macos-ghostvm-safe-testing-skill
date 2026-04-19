from __future__ import annotations

import importlib.util
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_guest_privacy_seed.py"
SPEC = importlib.util.spec_from_file_location("ghostvm_guest_privacy_seed", MODULE_PATH)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)


class TestGuestPrivacySeed(unittest.TestCase):
    def test_write_local_network_defaults_merges_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            prefs = (
                data_root
                / "private"
                / "var"
                / "root"
                / "Library"
                / "Preferences"
                / "com.apple.network.local-network.plist"
            )
            prefs.parent.mkdir(parents=True, exist_ok=True)
            with prefs.open("wb") as fh:
                plistlib.dump(
                    {
                        "AllowedEthernetLocalNetworkAddresses": ["10.0.0.0/8"],
                        "AllowedWiFiLocalNetworkAddresses": ["192.168.0.0/16"],
                    },
                    fh,
                )

            seed.write_local_network_defaults(data_root, ["10.0.0.0/8", "172.16.0.0/12"])

            with prefs.open("rb") as fh:
                payload = plistlib.load(fh)

            self.assertEqual(
                payload["AllowedEthernetLocalNetworkAddresses"],
                ["10.0.0.0/8", "172.16.0.0/12"],
            )
            self.assertEqual(
                payload["AllowedWiFiLocalNetworkAddresses"],
                ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"],
            )

    def test_apply_seed_patches_system_and_requested_user_databases(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            system_db = data_root / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
            user_db = (
                data_root
                / "Users"
                / "agent"
                / "Library"
                / "Application Support"
                / "com.apple.TCC"
                / "TCC.db"
            )
            self._make_modern_tcc_db(system_db)
            self._make_modern_tcc_db(user_db)

            rc = seed.apply_seed(
                data_root,
                skip_local_network=False,
                skip_tcc=False,
                cidrs=["10.0.0.0/8"],
                users=["agent"],
                appletargets=["com.apple.systemevents", "com.apple.TextEdit"],
                tcc_clients=["/usr/bin/osascript"],
            )
            self.assertEqual(rc, 0)

            expected_services = {
                "kTCCServiceAccessibility",
                "kTCCServiceScreenCapture",
                "kTCCServicePostEvent",
                "kTCCServiceAppleEvents",
            }
            for db_path in (system_db, user_db):
                with sqlite3.connect(db_path) as conn:
                    rows = conn.execute(
                        "SELECT service, client, indirect_object_identifier FROM access ORDER BY service, indirect_object_identifier"
                    ).fetchall()
                self.assertEqual({row[0] for row in rows}, expected_services)
                appleevents = [row for row in rows if row[0] == "kTCCServiceAppleEvents"]
                self.assertEqual(
                    {row[2] for row in appleevents},
                    {"com.apple.systemevents", "com.apple.TextEdit"},
                )

            prefs = (
                data_root
                / "private"
                / "var"
                / "root"
                / "Library"
                / "Preferences"
                / "com.apple.network.local-network.plist"
            )
            with prefs.open("rb") as fh:
                payload = plistlib.load(fh)
            self.assertEqual(payload["AllowedEthernetLocalNetworkAddresses"], ["10.0.0.0/8"])
            self.assertEqual(payload["AllowedWiFiLocalNetworkAddresses"], ["10.0.0.0/8"])

    def test_upsert_tcc_grants_supports_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "TCC.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE access ("
                    "service TEXT NOT NULL, "
                    "client TEXT NOT NULL, "
                    "client_type INTEGER NOT NULL, "
                    "allowed INTEGER NOT NULL, "
                    "prompt_count INTEGER NOT NULL, "
                    "csreq BLOB, "
                    "policy_id INTEGER, "
                    "indirect_object_identifier_type INTEGER, "
                    "indirect_object_identifier TEXT NOT NULL, "
                    "indirect_object_code_identity BLOB"
                    ")"
                )
                conn.commit()

            grants = seed.build_default_tcc_grants(
                ["/usr/bin/osascript"],
                ["com.apple.systemevents"],
            )
            patched = seed.upsert_tcc_grants(db_path, grants)
            self.assertEqual(patched, 4)

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT service, client, allowed, prompt_count, indirect_object_identifier FROM access"
                ).fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row[2] == 1 for row in rows))
            self.assertTrue(all(row[3] == 1 for row in rows))
            self.assertIn(
                ("kTCCServiceAppleEvents", "/usr/bin/osascript", 1, 1, "com.apple.systemevents"),
                rows,
            )

    def test_upsert_tcc_grants_ignores_unknown_nullable_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "TCC.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE access ("
                    "service TEXT NOT NULL, "
                    "client TEXT NOT NULL, "
                    "client_type INTEGER NOT NULL, "
                    "auth_value INTEGER NOT NULL, "
                    "auth_reason INTEGER NOT NULL, "
                    "auth_version INTEGER NOT NULL, "
                    "csreq BLOB, "
                    "policy_id INTEGER, "
                    "indirect_object_identifier_type INTEGER, "
                    "indirect_object_identifier TEXT NOT NULL, "
                    "indirect_object_code_identity BLOB, "
                    "flags INTEGER, "
                    "last_modified INTEGER, "
                    "last_reminded INTEGER, "
                    "unknown_column TEXT"
                    ")"
                )
                conn.commit()

            grants = seed.build_default_tcc_grants(
                ["/usr/bin/osascript"],
                ["com.apple.systemevents"],
            )
            patched = seed.upsert_tcc_grants(db_path, grants)
            self.assertEqual(patched, 4)

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT unknown_column FROM access").fetchall()
            self.assertEqual([row[0] for row in rows], [None] * 4)

    def test_upsert_tcc_grants_rejects_unknown_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "TCC.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE access ("
                    "service TEXT NOT NULL, "
                    "client TEXT NOT NULL, "
                    "client_type INTEGER NOT NULL, "
                    "auth_value INTEGER NOT NULL, "
                    "auth_reason INTEGER NOT NULL, "
                    "auth_version INTEGER NOT NULL, "
                    "csreq BLOB, "
                    "policy_id INTEGER, "
                    "indirect_object_identifier_type INTEGER, "
                    "indirect_object_identifier TEXT NOT NULL, "
                    "indirect_object_code_identity BLOB, "
                    "flags INTEGER, "
                    "last_modified INTEGER, "
                    "last_reminded INTEGER, "
                    "unknown_column TEXT NOT NULL"
                    ")"
                )
                conn.commit()

            grants = seed.build_default_tcc_grants(
                ["/usr/bin/osascript"],
                ["com.apple.systemevents"],
            )
            with self.assertRaises(seed.SeedError):
                seed.upsert_tcc_grants(db_path, grants)

    @staticmethod
    def _make_modern_tcc_db(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE access ("
                "service TEXT NOT NULL, "
                "client TEXT NOT NULL, "
                "client_type INTEGER NOT NULL, "
                "auth_value INTEGER NOT NULL, "
                "auth_reason INTEGER NOT NULL, "
                "auth_version INTEGER NOT NULL, "
                "csreq BLOB, "
                "policy_id INTEGER, "
                "indirect_object_identifier_type INTEGER, "
                "indirect_object_identifier TEXT NOT NULL, "
                "indirect_object_code_identity BLOB, "
                "flags INTEGER, "
                "last_modified INTEGER, "
                "last_reminded INTEGER"
                ")"
            )
            conn.commit()


if __name__ == "__main__":
    unittest.main()
