#!/usr/bin/env python3
from __future__ import annotations

import argparse
import plistlib
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_LOCAL_NETWORK_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "fc00::/7",
    "fe80::/10",
]
DEFAULT_APPLEEVENT_TARGETS = [
    "com.apple.systemevents",
    "com.apple.finder",
    "com.apple.Safari",
    "com.apple.mail",
]
DEFAULT_TCC_CLIENTS = [
    "/usr/bin/osascript",
    "/usr/libexec/sshd-keygen-wrapper",
]
DEFAULT_TCC_BUNDLE_IDS = [
    "org.ghostvm.com.ghostvm.guest-tools",
]
DEFAULT_TCC_SERVICES = [
    "kTCCServiceAccessibility",
    "kTCCServiceScreenCapture",
    "kTCCServicePostEvent",
]
DEFAULT_XCODE_TCC_SERVICES = [
    "kTCCServiceDeveloperTool",
    "kTCCServiceListenEvent",
]
DEFAULT_XCODE_APP_PATHS = ["/Applications/Xcode.app"]
XCODE_EXECUTABLE_RELATIVE_PATHS = [
    "Contents/Developer/usr/bin/xcodebuild",
    "Contents/Developer/usr/bin/xcrun",
]
XCODE_STUB_TCC_CLIENTS = [
    "/usr/bin/xcodebuild",
    "/usr/bin/xcrun",
]


@dataclass(frozen=True)
class AttachedDisk:
    root_device: str
    data_mountpoint: Path


@dataclass(frozen=True)
class TCCGrant:
    service: str
    client: str
    client_type: int = 1
    receiver: str | None = None


class SeedError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, file=sys.stderr)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Offline-seed a GhostVM guest image for unattended automation by "
            "writing Local Network exemptions and baseline TCC grants while the VM is stopped."
        )
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle", help="Path to <Name>.GhostVM bundle")
    src.add_argument(
        "--mounted-root",
        help=(
            "Mounted guest data-volume root. Skips disk-image attach and writes directly here. "
            "Useful for testing or when the image is already mounted."
        ),
    )
    ap.add_argument(
        "--skip-local-network",
        action="store_true",
        help="Do not write Local Network CIDR exemptions.",
    )
    ap.add_argument(
        "--skip-tcc",
        action="store_true",
        help="Do not patch TCC.db files.",
    )
    ap.add_argument(
        "--skip-safari-js-apple-events",
        action="store_true",
        help=(
            "Do not enable Safari's Allow JavaScript from Apple Events preference "
            "for detected or selected guest users."
        ),
    )
    ap.add_argument(
        "--cidr",
        action="append",
        dest="cidrs",
        help=(
            "CIDR to exempt from Local Network checks. Repeatable. "
            "Values are added to the default CIDR set."
        ),
    )
    ap.add_argument(
        "--user",
        action="append",
        dest="users",
        help=(
            "Guest username whose per-user TCC.db should be patched. Repeatable. "
            "Defaults to auto-detected users with an existing TCC.db."
        ),
    )
    ap.add_argument(
        "--appleevent-target",
        action="append",
        dest="appleevent_targets",
        help=(
            "Bundle identifier to allow for AppleEvents grants. Repeatable. "
            "Values are added to the default set (System Events, Finder, Safari, Mail)."
        ),
    )
    ap.add_argument(
        "--tcc-client",
        action="append",
        dest="tcc_clients",
        help=(
            "Absolute executable path to grant in TCC.db. Repeatable. "
            "Values are added to the defaults (/usr/bin/osascript and /usr/libexec/sshd-keygen-wrapper)."
        ),
    )
    ap.add_argument(
        "--tcc-bundle-id",
        action="append",
        dest="tcc_bundle_ids",
        help=(
            "Bundle identifier to grant in TCC.db. Repeatable. "
            "Values are added to the defaults (org.ghostvm.com.ghostvm.guest-tools / GhostTools)."
        ),
    )
    ap.add_argument(
        "--tcc-service",
        action="append",
        dest="tcc_services",
        help=(
            "TCC service to grant for path and bundle-id clients. Repeatable. "
            "Values are added to the defaults (Accessibility, ScreenCapture, PostEvent)."
        ),
    )
    ap.add_argument(
        "--xcode-ui-testing",
        action="store_true",
        help=(
            "Also seed common Xcode macOS UI-testing TCC clients, including "
            "Xcode.app, Xcode Helper.app, xcodebuild, and xcrun candidates when present in the guest image."
        ),
    )
    ap.add_argument(
        "--xcode-app",
        action="append",
        dest="xcode_apps",
        help=(
            "Guest path to an Xcode.app bundle used for --xcode-ui-testing. Repeatable. "
            "Default: /Applications/Xcode.app."
        ),
    )
    return ap.parse_args()


def resolve_existing_path(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise SeedError(f"Path not found: {resolved}")
    return resolved


def run(
    cmd: list[str], *, binary: bool = False
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(cmd, capture_output=True, text=not binary)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if binary else proc.stderr
        stdout = proc.stdout.decode("utf-8", errors="replace") if binary else proc.stdout
        details = stderr.strip() or stdout.strip() or f"exit {proc.returncode}"
        raise SeedError(f"Command failed: {' '.join(cmd)}\n{details}")
    return proc


def normalize_role(raw: object) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return {str(raw)}


def choose_data_mountpoint(candidates: list[Path]) -> Path | None:
    scored: list[tuple[int, Path]] = []
    for path in candidates:
        score = 0
        if path.name == "Data":
            score += 30
        if path.name.endswith(" - Data"):
            score += 25
        if (path / "Users").is_dir():
            score += 15
        if (path / "private").is_dir():
            score += 15
        if (path / "Library").is_dir():
            score += 10
        if (path / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db").is_file():
            score += 40
        if (path / "System").is_dir() and not (path / "Users").is_dir():
            score -= 10
        scored.append((score, path))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    best_score, best_path = scored[0]
    return best_path if best_score > 0 else None


@contextmanager
def mounted_data_root(bundle: Path) -> Iterator[Path]:
    if sys.platform != "darwin":
        raise SeedError(
            "Offline disk mounting requires a macOS host. Use --mounted-root when testing elsewhere."
        )

    disk_image = bundle / "disk.img"
    if not disk_image.exists():
        raise SeedError(f"disk.img not found in bundle: {disk_image}")

    attached_here = False
    entities: list[dict[str, object]] | None = None
    try:
        attach = run(
            ["hdiutil", "attach", "-plist", "-nobrowse", "-owners", "off", str(disk_image)],
            binary=True,
        )
        attached_here = True
        payload = plistlib.loads(attach.stdout)
        entities = payload.get("system-entities", [])
    except SeedError:
        try:
            info = plistlib.loads(run(["hdiutil", "info", "-plist"], binary=True).stdout)
        except SeedError as exc:
            raise SeedError(
                "hdiutil attach failed, and querying existing attachments also failed"
            ) from exc

        disk_resolved = disk_image.resolve()
        for image in info.get("images", []):
            img_path = image.get("image-path")
            if not img_path:
                continue
            try:
                if Path(str(img_path)).resolve() != disk_resolved:
                    continue
            except OSError:
                continue
            entities = image.get("system-entities", [])
            break

        if entities is None:
            raise
    dev_entries = [
        str(entity.get("dev-entry", "")) for entity in entities if entity.get("dev-entry")
    ]
    if not dev_entries:
        raise SeedError("hdiutil attach did not return any device entries")

    mountpoints = [
        Path(str(entity["mount-point"])) for entity in entities if entity.get("mount-point")
    ]
    root_device = min(dev_entries, key=len)

    if not mountpoints:
        for dev in sorted(dev_entries, key=len, reverse=True):
            info = plistlib.loads(run(["diskutil", "info", "-plist", dev], binary=True).stdout)
            roles = normalize_role(info.get("APFSVolumeRole"))
            volume_name = str(info.get("VolumeName", ""))
            if "Data" in roles or volume_name.endswith(" - Data"):
                if not info.get("MountPoint"):
                    run(["diskutil", "mount", "readWrite", dev])
                    info = plistlib.loads(
                        run(["diskutil", "info", "-plist", dev], binary=True).stdout
                    )
                mount_point = info.get("MountPoint")
                if mount_point:
                    mountpoints.append(Path(str(mount_point)))

    data_root = choose_data_mountpoint(mountpoints)
    if data_root is None:
        raise SeedError(
            "Could not identify the guest data volume after mounting disk.img. "
            "Mount the image manually and rerun with --mounted-root <path>."
        )

    say(f"[seed] mounted guest data volume at {data_root}")
    try:
        yield data_root
    finally:
        detach_cmd = ["hdiutil", "detach", root_device]
        proc = subprocess.run(detach_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            proc = subprocess.run([*detach_cmd, "-force"], capture_output=True, text=True)
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
                if attached_here:
                    raise SeedError(f"Failed to detach mounted disk image {root_device}: {detail}")
                say(
                    f"[seed] warning: unable to detach pre-attached disk image {root_device}: {detail}"
                )


def ensure_absolute_exec_paths(paths: list[str]) -> list[str]:
    if not paths:
        return []
    invalid = [path for path in paths if not path.startswith("/")]
    if invalid:
        raise SeedError(f"--tcc-client values must be absolute paths: {', '.join(invalid)}")
    return paths


def merge_unique(existing: list[str], desired: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in [*existing, *desired]:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def atomic_write_plist(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=True)
    tmp.replace(path)


def write_local_network_defaults(data_root: Path, cidrs: list[str]) -> Path:
    prefs_path = (
        data_root
        / "private"
        / "var"
        / "root"
        / "Library"
        / "Preferences"
        / "com.apple.network.local-network.plist"
    )
    payload: dict[str, object] = {}
    if prefs_path.exists():
        with prefs_path.open("rb") as fh:
            payload = plistlib.load(fh)

    for key in (
        "AllowedEthernetLocalNetworkAddresses",
        "AllowedWiFiLocalNetworkAddresses",
    ):
        current = list(payload.get(key, [])) if isinstance(payload.get(key, []), list) else []
        payload[key] = merge_unique([str(item) for item in current], cidrs)

    atomic_write_plist(prefs_path, payload)
    return prefs_path


def guest_user_homes(
    data_root: Path, explicit_users: list[str] | None
) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    homes: list[Path] = []

    def home_for(name: str) -> Path:
        if name == "root":
            return data_root / "private" / "var" / "root"
        return data_root / "Users" / name

    if explicit_users:
        for name in explicit_users:
            home = home_for(name)
            if home.is_dir():
                homes.append(home)
            else:
                warnings.append(f"requested user '{name}' has no home directory at {home}")
        return homes, warnings

    users_dir = data_root / "Users"
    if users_dir.is_dir():
        for home in sorted(users_dir.iterdir()):
            if not home.is_dir() or home.name in {"Shared", ".localized"}:
                continue
            homes.append(home)

    if not homes:
        warnings.append("no guest user home directories were found for Safari preferences")
    return homes, warnings


def write_safari_js_from_apple_events_preference(
    data_root: Path, explicit_users: list[str] | None
) -> tuple[list[Path], list[str]]:
    homes, warnings = guest_user_homes(data_root, explicit_users)
    written: list[Path] = []

    for home in homes:
        prefs_path = (
            home
            / "Library"
            / "Containers"
            / "com.apple.Safari"
            / "Data"
            / "Library"
            / "Preferences"
            / "com.apple.Safari.plist"
        )
        payload: dict[str, object] = {}
        if prefs_path.exists():
            with prefs_path.open("rb") as fh:
                payload = plistlib.load(fh)
        payload["AllowJavaScriptFromAppleEvents"] = True
        atomic_write_plist(prefs_path, payload)
        written.append(prefs_path)

    return written, warnings


def mounted_path_for_guest_path(data_root: Path, guest_path: str) -> Path:
    if not guest_path.startswith("/"):
        raise SeedError(f"guest paths must be absolute: {guest_path}")
    return data_root / guest_path.lstrip("/")


def read_bundle_info(bundle_path: Path) -> dict[str, object]:
    info_path = bundle_path / "Contents" / "Info.plist"
    if not info_path.exists():
        return {}
    try:
        with info_path.open("rb") as fh:
            payload = plistlib.load(fh)
    except Exception as exc:  # plistlib.InvalidFileException is not present on older Python.
        raise SeedError(f"Failed to read bundle Info.plist at {info_path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def bundle_identifier(bundle_path: Path) -> str | None:
    value = read_bundle_info(bundle_path).get("CFBundleIdentifier")
    return str(value) if value else None


def bundle_executable_guest_path(bundle_path: Path, guest_bundle_path: str) -> str | None:
    value = read_bundle_info(bundle_path).get("CFBundleExecutable")
    if not value:
        return None
    exec_path = bundle_path / "Contents" / "MacOS" / str(value)
    if not exec_path.exists():
        return None
    return f"{guest_bundle_path.rstrip('/')}/Contents/MacOS/{value}"


def guest_path_for_mounted_path(data_root: Path, mounted_path: Path) -> str:
    try:
        relative = mounted_path.relative_to(data_root)
    except ValueError as exc:
        raise SeedError(f"mounted path is outside data root: {mounted_path}") from exc
    return f"/{relative.as_posix()}"


def xcode_app_bundles(mounted_xcode_app: Path) -> list[Path]:
    bundles = [mounted_xcode_app]
    for candidate in sorted(mounted_xcode_app.rglob("*.app")):
        if candidate != mounted_xcode_app and candidate.is_dir():
            bundles.append(candidate)
    return bundles


def xcode_ui_testing_tcc_candidates(
    data_root: Path, xcode_apps: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return (path clients, bundle-id clients, warnings) for macOS Xcode UI tests."""

    path_clients: list[str] = []
    bundle_ids: list[str] = []
    warnings: list[str] = []

    for guest_xcode_app in xcode_apps:
        mounted_xcode_app = mounted_path_for_guest_path(data_root, guest_xcode_app)
        if not mounted_xcode_app.exists():
            warnings.append(f"Xcode.app not found for --xcode-ui-testing: {guest_xcode_app}")
            continue

        for bundle_path in xcode_app_bundles(mounted_xcode_app):
            guest_bundle_path = guest_path_for_mounted_path(data_root, bundle_path)
            bundle_id = bundle_identifier(bundle_path)
            if bundle_id:
                if bundle_id.startswith("com.apple.dt.Xcode"):
                    bundle_ids.append(bundle_id)
            elif bundle_path == mounted_xcode_app:
                warnings.append(f"Could not read CFBundleIdentifier for {guest_xcode_app}")

            bundle_exec = bundle_executable_guest_path(bundle_path, guest_bundle_path)
            if bundle_exec and (bundle_path == mounted_xcode_app or bundle_id in bundle_ids):
                path_clients.append(bundle_exec)

        for relative_path in XCODE_EXECUTABLE_RELATIVE_PATHS:
            mounted_exec = mounted_xcode_app / relative_path
            if mounted_exec.exists():
                path_clients.append(f"{guest_xcode_app.rstrip('/')}/{relative_path}")

    # /usr/bin lives on the sealed system volume, not under the mounted Data
    # volume we patch offline. Seed the Xcode command-line stubs unconditionally.
    path_clients.extend(XCODE_STUB_TCC_CLIENTS)

    return merge_unique([], path_clients), merge_unique([], bundle_ids), warnings


def existing_user_db_paths(
    data_root: Path, explicit_users: list[str] | None
) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    paths: list[Path] = []

    def user_db_for(name: str) -> Path:
        if name == "root":
            return (
                data_root
                / "private"
                / "var"
                / "root"
                / "Library"
                / "Application Support"
                / "com.apple.TCC"
                / "TCC.db"
            )
        return (
            data_root
            / "Users"
            / name
            / "Library"
            / "Application Support"
            / "com.apple.TCC"
            / "TCC.db"
        )

    if explicit_users:
        for name in explicit_users:
            db_path = user_db_for(name)
            if db_path.exists():
                paths.append(db_path)
            else:
                warnings.append(f"requested user '{name}' has no TCC.db at {db_path}")
        return paths, warnings

    users_dir = data_root / "Users"
    if users_dir.is_dir():
        for home in sorted(users_dir.iterdir()):
            if not home.is_dir() or home.name in {"Shared", ".localized"}:
                continue
            db_path = home / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
            if db_path.exists():
                paths.append(db_path)

    root_db = user_db_for("root")
    if root_db.exists():
        paths.append(root_db)

    if not paths:
        warnings.append(
            "no per-user TCC.db files were found; only the system database will be patched"
        )
    return paths, warnings


def read_access_columns(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    rows = conn.execute("PRAGMA table_info(access)").fetchall()
    if not rows:
        raise SeedError("TCC.db is missing the access table")

    handled = {
        "service",
        "client",
        "client_type",
        "allowed",
        "prompt_count",
        "csreq",
        "policy_id",
        "indirect_object_identifier_type",
        "indirect_object_identifier",
        "indirect_object_code_identity",
        "auth_value",
        "auth_reason",
        "auth_version",
        "flags",
        "last_modified",
        "last_reminded",
    }

    columns = [str(row[1]) for row in rows]
    writable = [col for col in columns if col in handled]
    missing_required: list[str] = []
    for _, name, _, notnull, default_value, _ in rows:
        col_name = str(name)
        if col_name in handled:
            continue
        if int(notnull) and default_value is None:
            missing_required.append(col_name)
    return writable, missing_required


def values_for_grant(columns: list[str], grant: TCCGrant) -> list[object]:
    now = int(time.time())
    receiver = grant.receiver
    values: dict[str, object] = {
        "service": grant.service,
        "client": grant.client,
        "client_type": grant.client_type,
        "allowed": 1,
        "prompt_count": 1,
        "csreq": None,
        "policy_id": None,
        "indirect_object_identifier_type": 0 if receiver else None,
        "indirect_object_identifier": receiver or "UNUSED",
        "indirect_object_code_identity": None,
        "auth_value": 2,
        "auth_reason": 0,
        "auth_version": 1,
        "flags": 0,
        "last_modified": now,
        "last_reminded": now,
    }
    return [values.get(column) for column in columns]


def upsert_tcc_grants(db_path: Path, grants: list[TCCGrant]) -> int:
    with sqlite3.connect(db_path) as conn:
        columns, missing_required = read_access_columns(conn)
        if missing_required:
            missing = ", ".join(missing_required)
            raise SeedError(
                f"Unsupported TCC.db schema at {db_path}; required columns not handled: {missing}"
            )

        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        sql = f"INSERT OR REPLACE INTO access ({column_sql}) VALUES ({placeholders})"
        cursor = conn.cursor()
        for grant in grants:
            cursor.execute(sql, values_for_grant(columns, grant))
        conn.commit()
        return len(grants)


def build_default_tcc_grants(
    clients: list[str],
    receivers: list[str],
    services: list[str] | None = None,
) -> list[TCCGrant]:
    services = services or DEFAULT_TCC_SERVICES
    grants: list[TCCGrant] = []
    for client in clients:
        for service in services:
            grants.append(TCCGrant(service=service, client=client, client_type=1))
        for receiver in receivers:
            grants.append(
                TCCGrant(
                    service="kTCCServiceAppleEvents",
                    client=client,
                    client_type=1,
                    receiver=receiver,
                )
            )
    return grants


def build_bundle_id_tcc_grants(
    bundle_ids: list[str],
    receivers: list[str],
    services: list[str] | None = None,
) -> list[TCCGrant]:
    services = services or DEFAULT_TCC_SERVICES
    grants: list[TCCGrant] = []
    for bundle_id in bundle_ids:
        for service in services:
            grants.append(TCCGrant(service=service, client=bundle_id, client_type=0))
        for receiver in receivers:
            grants.append(
                TCCGrant(
                    service="kTCCServiceAppleEvents",
                    client=bundle_id,
                    client_type=0,
                    receiver=receiver,
                )
            )
    return grants


def apply_seed(
    data_root: Path,
    *,
    skip_local_network: bool,
    skip_tcc: bool,
    skip_safari_js_apple_events: bool,
    cidrs: list[str],
    users: list[str] | None,
    appletargets: list[str],
    tcc_clients: list[str],
    tcc_bundle_ids: list[str],
    tcc_services: list[str] | None = None,
    xcode_ui_testing: bool = False,
    xcode_apps: list[str] | None = None,
) -> int:
    say(f"[seed] data_root={data_root}")
    warnings: list[str] = []
    active_tcc_services = merge_unique(DEFAULT_TCC_SERVICES.copy(), tcc_services or [])

    if not skip_local_network:
        prefs_path = write_local_network_defaults(data_root, cidrs)
        say(f"[seed] wrote Local Network CIDR exemptions: {prefs_path}")
        say(f"[seed] cidrs={' '.join(cidrs)}")

    if not skip_safari_js_apple_events:
        prefs_paths, safari_warnings = write_safari_js_from_apple_events_preference(
            data_root, users
        )
        warnings.extend(safari_warnings)
        for prefs_path in prefs_paths:
            say(f"[seed] enabled Safari JavaScript from Apple Events: {prefs_path}")

    if not skip_tcc:
        system_db = data_root / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
        if not system_db.exists():
            raise SeedError(
                f"System TCC.db not found at {system_db}. Is this the guest data volume root?"
            )

        if xcode_ui_testing:
            active_tcc_services = merge_unique(
                active_tcc_services, DEFAULT_XCODE_TCC_SERVICES.copy()
            )
            xcode_tcc_clients, xcode_tcc_bundle_ids, xcode_warnings = (
                xcode_ui_testing_tcc_candidates(data_root, xcode_apps or DEFAULT_XCODE_APP_PATHS)
            )
            tcc_clients = merge_unique(tcc_clients, xcode_tcc_clients)
            tcc_bundle_ids = merge_unique(tcc_bundle_ids, xcode_tcc_bundle_ids)
            warnings.extend(xcode_warnings)
            if xcode_tcc_clients or xcode_tcc_bundle_ids:
                say("[seed] added Xcode UI-testing TCC candidates")
                for client in xcode_tcc_clients:
                    say(f"[seed]   tcc-client={client}")
                for bundle_id in xcode_tcc_bundle_ids:
                    say(f"[seed]   tcc-bundle-id={bundle_id}")

        grants = [
            *build_default_tcc_grants(tcc_clients, appletargets, active_tcc_services),
            *build_bundle_id_tcc_grants(tcc_bundle_ids, appletargets, active_tcc_services),
        ]
        patched = upsert_tcc_grants(system_db, grants)
        say(f"[seed] patched system TCC.db: {system_db} ({patched} grants)")

        user_db_paths, user_warnings = existing_user_db_paths(data_root, users)
        warnings.extend(user_warnings)
        for db_path in user_db_paths:
            patched = upsert_tcc_grants(db_path, grants)
            say(f"[seed] patched user TCC.db: {db_path} ({patched} grants)")

    for warning in warnings:
        say(f"[seed] warning: {warning}")
    return 0


def main() -> int:
    ns = parse_args()
    cidrs = merge_unique(
        DEFAULT_LOCAL_NETWORK_CIDRS.copy(), [str(item) for item in (ns.cidrs or [])]
    )
    appletargets = merge_unique(
        DEFAULT_APPLEEVENT_TARGETS.copy(), [str(item) for item in (ns.appleevent_targets or [])]
    )
    tcc_clients = merge_unique(
        DEFAULT_TCC_CLIENTS.copy(),
        ensure_absolute_exec_paths([str(item) for item in (ns.tcc_clients or [])]),
    )
    tcc_bundle_ids = merge_unique(
        DEFAULT_TCC_BUNDLE_IDS.copy(), [str(item) for item in (ns.tcc_bundle_ids or [])]
    )
    tcc_services = merge_unique(
        DEFAULT_TCC_SERVICES.copy(), [str(item) for item in (ns.tcc_services or [])]
    )

    xcode_apps = merge_unique(
        DEFAULT_XCODE_APP_PATHS.copy(), [str(item) for item in (ns.xcode_apps or [])]
    )
    xcode_ui_testing = bool(ns.xcode_ui_testing or ns.xcode_apps)

    if ns.mounted_root:
        data_root = resolve_existing_path(ns.mounted_root)
        return apply_seed(
            data_root,
            skip_local_network=ns.skip_local_network,
            skip_tcc=ns.skip_tcc,
            skip_safari_js_apple_events=ns.skip_safari_js_apple_events,
            cidrs=cidrs,
            users=ns.users,
            appletargets=appletargets,
            tcc_clients=tcc_clients,
            tcc_bundle_ids=tcc_bundle_ids,
            tcc_services=tcc_services,
            xcode_ui_testing=xcode_ui_testing,
            xcode_apps=xcode_apps,
        )

    bundle = resolve_existing_path(ns.bundle)
    with mounted_data_root(bundle) as data_root:
        return apply_seed(
            data_root,
            skip_local_network=ns.skip_local_network,
            skip_tcc=ns.skip_tcc,
            skip_safari_js_apple_events=ns.skip_safari_js_apple_events,
            cidrs=cidrs,
            users=ns.users,
            appletargets=appletargets,
            tcc_clients=tcc_clients,
            tcc_bundle_ids=tcc_bundle_ids,
            tcc_services=tcc_services,
            xcode_ui_testing=xcode_ui_testing,
            xcode_apps=xcode_apps,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        say(f"ERROR: {exc}")
        raise SystemExit(1)
