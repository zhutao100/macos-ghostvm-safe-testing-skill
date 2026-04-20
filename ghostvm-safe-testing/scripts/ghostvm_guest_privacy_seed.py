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


def build_default_tcc_grants(clients: list[str], receivers: list[str]) -> list[TCCGrant]:
    grants: list[TCCGrant] = []
    for client in clients:
        for service in DEFAULT_TCC_SERVICES:
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


def build_bundle_id_tcc_grants(bundle_ids: list[str], receivers: list[str]) -> list[TCCGrant]:
    grants: list[TCCGrant] = []
    for bundle_id in bundle_ids:
        for service in DEFAULT_TCC_SERVICES:
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
    cidrs: list[str],
    users: list[str] | None,
    appletargets: list[str],
    tcc_clients: list[str],
    tcc_bundle_ids: list[str],
) -> int:
    say(f"[seed] data_root={data_root}")
    warnings: list[str] = []

    if not skip_local_network:
        prefs_path = write_local_network_defaults(data_root, cidrs)
        say(f"[seed] wrote Local Network CIDR exemptions: {prefs_path}")
        say(f"[seed] cidrs={' '.join(cidrs)}")

    if not skip_tcc:
        system_db = data_root / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
        if not system_db.exists():
            raise SeedError(
                f"System TCC.db not found at {system_db}. Is this the guest data volume root?"
            )

        grants = [
            *build_default_tcc_grants(tcc_clients, appletargets),
            *build_bundle_id_tcc_grants(tcc_bundle_ids, appletargets),
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

    if ns.mounted_root:
        data_root = resolve_existing_path(ns.mounted_root)
        return apply_seed(
            data_root,
            skip_local_network=ns.skip_local_network,
            skip_tcc=ns.skip_tcc,
            cidrs=cidrs,
            users=ns.users,
            appletargets=appletargets,
            tcc_clients=tcc_clients,
            tcc_bundle_ids=tcc_bundle_ids,
        )

    bundle = resolve_existing_path(ns.bundle)
    with mounted_data_root(bundle) as data_root:
        return apply_seed(
            data_root,
            skip_local_network=ns.skip_local_network,
            skip_tcc=ns.skip_tcc,
            cidrs=cidrs,
            users=ns.users,
            appletargets=appletargets,
            tcc_clients=tcc_clients,
            tcc_bundle_ids=tcc_bundle_ids,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        say(f"ERROR: {exc}")
        raise SystemExit(1)
