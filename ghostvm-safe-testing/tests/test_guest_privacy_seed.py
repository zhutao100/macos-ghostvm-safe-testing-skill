from __future__ import annotations

import importlib.util
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_guest_privacy_seed.py"
SPEC = importlib.util.spec_from_file_location("ghostvm_guest_privacy_seed", MODULE_PATH)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)


class TestGuestPrivacySeed(unittest.TestCase):
    def test_mount_uses_format_specific_attach_and_cleanup(self) -> None:
        for signature, device in ((b"shdw", "disk42"), (b"\0" * 4, "/dev/disk42")):
            with self.subTest(signature=signature), tempfile.TemporaryDirectory() as directory:
                bundle = Path(directory)
                (bundle / "disk.img").write_bytes(signature)
                root = bundle / "Data"
                root.mkdir()
                payload = plistlib.dumps(
                    {"system-entities": [{"dev-entry": device, "mount-point": str(root)}]}
                )
                with (
                    mock.patch.object(seed.sys, "platform", "darwin"),
                    mock.patch.object(
                        seed, "run", return_value=mock.Mock(stdout=payload)
                    ) as attach,
                    mock.patch.object(
                        seed.subprocess, "run", return_value=mock.Mock(returncode=0)
                    ) as detach,
                ):
                    with seed.mounted_data_root(bundle) as mounted:
                        self.assertEqual(mounted, root)
                    command = attach.call_args.args[0]
                    if signature == b"shdw":
                        self.assertEqual(command[:3], ["diskutil", "image", "attach"])
                        self.assertIn("noowners", command)
                        expected_detach = ["diskutil", "eject", "/dev/disk42"]
                    else:
                        self.assertEqual(command[:2], ["hdiutil", "attach"])
                        expected_detach = ["hdiutil", "detach", "/dev/disk42"]
                    self.assertEqual(detach.call_args.args[0], expected_detach)

    def test_asif_discovery_failure_still_ejects_owned_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "disk.img").write_bytes(b"shdw")
            payload = plistlib.dumps(
                {"system-entities": [{"dev-entry": "disk42", "mount-point": "/nonexistent"}]}
            )
            with (
                mock.patch.object(seed.sys, "platform", "darwin"),
                mock.patch.object(seed, "run", return_value=mock.Mock(stdout=payload)),
                mock.patch.object(
                    seed.subprocess, "run", return_value=mock.Mock(returncode=0)
                ) as detach,
            ):
                with self.assertRaisesRegex(seed.SeedError, "Could not identify"):
                    with seed.mounted_data_root(bundle):
                        self.fail("missing data volume must not be yielded")
                self.assertEqual(detach.call_args.args[0], ["diskutil", "eject", "/dev/disk42"])

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
                skip_safari_js_apple_events=False,
                cidrs=["10.0.0.0/8"],
                users=["agent"],
                appletargets=["com.apple.systemevents", "com.apple.TextEdit"],
                tcc_clients=["/usr/bin/osascript"],
                tcc_bundle_ids=[],
                xcode_ui_testing=False,
                xcode_apps=[],
            )
            self.assertEqual(rc, 0)

            expected_services = {
                "kTCCServiceAccessibility",
                "kTCCServiceScreenCapture",
                "kTCCServicePostEvent",
                "kTCCServiceAppleEvents",
            }
            for db_path in (system_db, user_db):
                with closing(sqlite3.connect(db_path)) as conn, conn:
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

            safari_prefs = (
                data_root
                / "Users"
                / "agent"
                / "Library"
                / "Containers"
                / "com.apple.Safari"
                / "Data"
                / "Library"
                / "Preferences"
                / "com.apple.Safari.plist"
            )
            with safari_prefs.open("rb") as fh:
                payload = plistlib.load(fh)
            self.assertIs(payload["AllowJavaScriptFromAppleEvents"], True)

    def test_xcode_ui_testing_candidates_include_xcode_helper_and_xcodebuild(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            xcode_app = data_root / "Applications" / "Xcode.app"
            self._write_bundle(xcode_app, "com.apple.dt.Xcode", "Xcode")
            xcodebuild = xcode_app / "Contents" / "Developer" / "usr" / "bin" / "xcodebuild"
            xcodebuild.parent.mkdir(parents=True, exist_ok=True)
            xcodebuild.touch()
            xcrun = xcode_app / "Contents" / "Developer" / "usr" / "bin" / "xcrun"
            xcrun.touch()

            helper = (
                xcode_app
                / "Contents"
                / "Developer"
                / "Platforms"
                / "MacOSX.platform"
                / "Developer"
                / "Library"
                / "Xcode"
                / "Agents"
                / "Xcode Helper.app"
            )
            self._write_bundle(helper, "com.apple.dt.Xcode-Helper", "Xcode Helper")

            path_clients, bundle_ids, warnings = seed.xcode_ui_testing_tcc_candidates(
                data_root, ["/Applications/Xcode.app"]
            )

            self.assertEqual(warnings, [])
            self.assertIn("com.apple.dt.Xcode", bundle_ids)
            self.assertIn("com.apple.dt.Xcode-Helper", bundle_ids)
            self.assertIn("/Applications/Xcode.app/Contents/MacOS/Xcode", path_clients)
            self.assertIn(
                "/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild", path_clients
            )
            self.assertIn("/Applications/Xcode.app/Contents/Developer/usr/bin/xcrun", path_clients)
            self.assertIn(
                "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/Xcode Helper.app/Contents/MacOS/Xcode Helper",
                path_clients,
            )
            self.assertIn("/usr/bin/xcodebuild", path_clients)
            self.assertIn("/usr/bin/xcrun", path_clients)

    def test_apply_seed_can_patch_xcode_ui_testing_tcc_clients(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            system_db = data_root / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
            self._make_modern_tcc_db(system_db)
            self._write_bundle(
                data_root / "Applications" / "Xcode.app", "com.apple.dt.Xcode", "Xcode"
            )
            helper = (
                data_root
                / "Applications"
                / "Xcode.app"
                / "Contents"
                / "Developer"
                / "Platforms"
                / "MacOSX.platform"
                / "Developer"
                / "Library"
                / "Xcode"
                / "Agents"
                / "Xcode Helper.app"
            )
            self._write_bundle(helper, "com.apple.dt.Xcode-Helper", "Xcode Helper")
            xcrun = (
                data_root
                / "Applications"
                / "Xcode.app"
                / "Contents"
                / "Developer"
                / "usr"
                / "bin"
                / "xcrun"
            )
            xcrun.parent.mkdir(parents=True, exist_ok=True)
            xcrun.touch()

            rc = seed.apply_seed(
                data_root,
                skip_local_network=True,
                skip_tcc=False,
                skip_safari_js_apple_events=True,
                cidrs=[],
                users=[],
                appletargets=[],
                tcc_clients=[],
                tcc_bundle_ids=[],
                xcode_ui_testing=True,
                xcode_apps=["/Applications/Xcode.app"],
            )
            self.assertEqual(rc, 0)

            with closing(sqlite3.connect(system_db)) as conn, conn:
                rows = conn.execute("SELECT service, client, client_type FROM access").fetchall()

            self.assertIn(("kTCCServiceDeveloperTool", "com.apple.dt.Xcode", 0), rows)
            self.assertIn(("kTCCServiceListenEvent", "com.apple.dt.Xcode-Helper", 0), rows)
            self.assertIn(
                ("kTCCServiceDeveloperTool", "/Applications/Xcode.app/Contents/MacOS/Xcode", 1),
                rows,
            )
            self.assertIn(
                (
                    "kTCCServiceListenEvent",
                    "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/Xcode Helper.app/Contents/MacOS/Xcode Helper",
                    1,
                ),
                rows,
            )
            self.assertIn(
                (
                    "kTCCServiceDeveloperTool",
                    "/Applications/Xcode.app/Contents/Developer/usr/bin/xcrun",
                    1,
                ),
                rows,
            )

    def test_tcc_service_argument_extends_default_services(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            system_db = data_root / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
            self._make_modern_tcc_db(system_db)

            rc = seed.apply_seed(
                data_root,
                skip_local_network=True,
                skip_tcc=False,
                skip_safari_js_apple_events=True,
                cidrs=[],
                users=[],
                appletargets=[],
                tcc_clients=["/usr/bin/osascript"],
                tcc_bundle_ids=[],
                tcc_services=["kTCCServiceAccessibility", "kTCCServiceListenEvent"],
            )
            self.assertEqual(rc, 0)

            with closing(sqlite3.connect(system_db)) as conn, conn:
                services = {row[0] for row in conn.execute("SELECT service FROM access").fetchall()}

            self.assertEqual(
                services,
                {
                    "kTCCServiceAccessibility",
                    "kTCCServiceScreenCapture",
                    "kTCCServicePostEvent",
                    "kTCCServiceListenEvent",
                },
            )

    def test_write_safari_js_preference_preserves_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            prefs = (
                data_root
                / "Users"
                / "agent"
                / "Library"
                / "Containers"
                / "com.apple.Safari"
                / "Data"
                / "Library"
                / "Preferences"
                / "com.apple.Safari.plist"
            )
            prefs.parent.mkdir(parents=True, exist_ok=True)
            with prefs.open("wb") as fh:
                plistlib.dump({"IncludeDevelopMenu": True}, fh)

            written, warnings = seed.write_safari_js_from_apple_events_preference(
                data_root, ["agent"]
            )

            self.assertEqual(written, [prefs])
            self.assertEqual(warnings, [])
            with prefs.open("rb") as fh:
                payload = plistlib.load(fh)
            self.assertIs(payload["IncludeDevelopMenu"], True)
            self.assertIs(payload["AllowJavaScriptFromAppleEvents"], True)

    def test_upsert_tcc_grants_supports_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "TCC.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(db_path)) as conn, conn:
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

            with closing(sqlite3.connect(db_path)) as conn, conn:
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
            with closing(sqlite3.connect(db_path)) as conn, conn:
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

            with closing(sqlite3.connect(db_path)) as conn, conn:
                rows = conn.execute("SELECT unknown_column FROM access").fetchall()
            self.assertEqual([row[0] for row in rows], [None] * 4)

    def test_upsert_tcc_grants_rejects_unknown_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "TCC.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(db_path)) as conn, conn:
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

    def test_protected_stores_are_selected_by_metadata_uid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._make_protected_store(root, "selected", 501)
            self._make_protected_store(root, "other-user", 502)
            self._make_protected_store(root, "daemon", 248)
            paths, warnings = seed.existing_user_db_paths(root, ["agent"], [501])
            self.assertEqual(paths, [selected])
            self.assertEqual(warnings, [])

    def test_protected_store_autodetection_excludes_daemon_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._make_protected_store(root, "selected", 501)
            self._make_protected_store(root, "daemon", 248)
            paths, warnings = seed.existing_user_db_paths(root, None)
            self.assertEqual(paths, [selected])
            self.assertEqual(warnings, [])

    def test_protected_store_resolves_uid_from_guest_account_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._make_protected_store(root, "selected", 502)
            account = root / "private/var/db/dslocal/nodes/Default/users/agent.plist"
            account.parent.mkdir(parents=True)
            with account.open("wb") as stream:
                plistlib.dump({"uid": ["502"]}, stream)
            paths, _ = seed.existing_user_db_paths(root, ["agent"])
            self.assertEqual(paths, [selected])

    def test_protected_store_does_not_guess_uid_from_host_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_protected_store(root, "selected", 501)
            with self.assertRaisesRegex(seed.SeedError, "Pass --user-uid"):
                seed.existing_user_db_paths(root, ["agent"])
            with self.assertRaisesRegex(seed.SeedError, "No protected TCC store"):
                seed.existing_user_db_paths(root, ["agent"], [999])

    def test_new_defaulted_tcc_column_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "TCC.db"
            self._make_modern_tcc_db(db)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute(
                    "ALTER TABLE access ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0"
                )
            seed.upsert_tcc_grants(
                db, [seed.TCCGrant("kTCCServiceAccessibility", "/usr/bin/osascript")]
            )
            with closing(sqlite3.connect(db)) as connection, connection:
                self.assertEqual(
                    connection.execute("SELECT reminder_count FROM access").fetchone(), (0,)
                )

    def test_seed_closes_database_before_image_detach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "TCC.db"
            self._make_modern_tcc_db(db)
            connection = sqlite3.connect(db)
            with mock.patch.object(seed.sqlite3, "connect", return_value=connection):
                seed.upsert_tcc_grants(
                    db, [seed.TCCGrant("kTCCServiceAccessibility", "/usr/bin/osascript")]
                )
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")

    def _make_protected_store(self, root: Path, name: str, uid: int) -> Path:
        container = root / "private/var/containers/Data/ProtectedSystem" / name
        db = container / "Data/Library/Application Support/com.apple.TCC/TCC.db"
        self._make_modern_tcc_db(db)
        with (container / ".com.apple.containermanagerd.metadata.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "MCMMetadataIdentifier": "com.apple.tccd",
                    "MCMMetadataOwnership": {"uid": uid},
                },
                stream,
            )
        return db

    @staticmethod
    def _write_bundle(path: Path, bundle_id: str, executable: str) -> None:
        contents = path / "Contents"
        macos = contents / "MacOS"
        macos.mkdir(parents=True, exist_ok=True)
        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": executable}, fh)
        (macos / executable).touch()

    @staticmethod
    def _make_modern_tcc_db(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
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
