#!/usr/bin/env python3
from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path

import ghostvm_guest_privacy_seed as seed

SOFTWARE_UPDATE_FALSE_KEYS = {
    "AutomaticCheckEnabled": False,
    "AutomaticDownload": False,
    "AutomaticallyInstallMacOSUpdates": False,
    "AllowPreReleaseInstallation": False,
}
SECURITY_RESPONSE_FALSE_KEYS = {
    "ConfigDataInstall": False,
    "CriticalUpdateInstall": False,
}
COMMERCE_FALSE_KEYS = {
    "AutoUpdate": False,
    "AutoUpdateRestartRequired": False,
}
SCREENSAVER_PAYLOAD = {
    "askForPassword": 0,
    "askForPasswordDelay": 2_147_483_647,
    "idleTime": 0,
}
GLOBAL_PREFERENCES_PAYLOAD = {
    # Keep the guest usable for long-running remote exec/UI automation after login.
    "NSNavPanelExpandedStateForSaveMode": True,
    "NSNavPanelExpandedStateForSaveMode2": True,
    "AppleShowAllExtensions": True,
}
TIME_MACHINE_PAYLOAD = {
    "AutoBackup": False,
    "DoNotOfferNewDisksForBackup": True,
}


def _read_plist(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        payload = plistlib.load(fh)
    return payload if isinstance(payload, dict) else {}


def _merge_plist(path: Path, updates: dict[str, object]) -> None:
    payload = _read_plist(path)
    payload.update(updates)
    seed.atomic_write_plist(path, payload)


def write_software_update_preferences(
    data_root: Path, *, keep_security_responses: bool
) -> list[Path]:
    written: list[Path] = []

    software_update = data_root / "Library" / "Preferences" / "com.apple.SoftwareUpdate.plist"
    payload = dict(SOFTWARE_UPDATE_FALSE_KEYS)
    if not keep_security_responses:
        payload.update(SECURITY_RESPONSE_FALSE_KEYS)
    _merge_plist(software_update, payload)
    written.append(software_update)

    commerce = data_root / "Library" / "Preferences" / "com.apple.commerce.plist"
    _merge_plist(commerce, COMMERCE_FALSE_KEYS)
    written.append(commerce)

    return written


def write_screen_lock_preferences(
    data_root: Path, explicit_users: list[str] | None
) -> tuple[list[Path], list[str]]:
    homes, warnings = seed.guest_user_homes(data_root, explicit_users)
    warnings = [
        warning.replace("Safari preferences", "screen-lock preferences") for warning in warnings
    ]
    written: list[Path] = []

    # Include root because some early-boot/bootstrap scripts run as root and may inherit root defaults.
    root_home = data_root / "private" / "var" / "root"
    if root_home.is_dir() and root_home not in homes:
        homes.append(root_home)

    for home in homes:
        prefs = home / "Library" / "Preferences" / "com.apple.screensaver.plist"
        _merge_plist(prefs, SCREENSAVER_PAYLOAD)
        written.append(prefs)

        global_prefs = home / "Library" / "Preferences" / ".GlobalPreferences.plist"
        _merge_plist(global_prefs, GLOBAL_PREFERENCES_PAYLOAD)
        written.append(global_prefs)

    return written, warnings


def write_time_machine_preferences(data_root: Path) -> Path:
    prefs = data_root / "Library" / "Preferences" / "com.apple.TimeMachine.plist"
    _merge_plist(prefs, TIME_MACHINE_PAYLOAD)
    return prefs


def disable_spotlight_indexing(data_root: Path) -> Path:
    marker = data_root / ".metadata_never_index"
    marker.write_text(
        "GhostVM disposable automation image: skip Spotlight indexing.\n", encoding="utf-8"
    )
    return marker


def apply_tuning(
    data_root: Path,
    *,
    users: list[str] | None,
    skip_software_update: bool,
    keep_security_responses: bool,
    skip_screen_lock: bool,
    skip_time_machine: bool,
    skip_spotlight: bool,
) -> int:
    data_root = data_root.resolve()
    if not data_root.exists() or not data_root.is_dir():
        raise seed.SeedError(f"mounted data root not found: {data_root}")

    if not skip_software_update:
        for path in write_software_update_preferences(
            data_root, keep_security_responses=keep_security_responses
        ):
            seed.say(f"[tune] wrote software-update automation preference: {path}")

    if not skip_screen_lock:
        written, warnings = write_screen_lock_preferences(data_root, users)
        for warning in warnings:
            seed.say(f"[tune] warning: {warning}")
        for path in written:
            seed.say(f"[tune] wrote idle/lock preference: {path}")

    if not skip_time_machine:
        path = write_time_machine_preferences(data_root)
        seed.say(f"[tune] wrote Time Machine prompt preference: {path}")

    if not skip_spotlight:
        path = disable_spotlight_indexing(data_root)
        seed.say(f"[tune] wrote Spotlight opt-out marker: {path}")

    seed.say("[tune] automation tuning complete")
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Offline-tune a stopped GhostVM macOS data volume for disposable automation. "
            "Use this after installing vanilla macOS and before creating an automation-ready snapshot."
        )
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle", help="Path to <Name>.GhostVM bundle")
    src.add_argument(
        "--mounted-root",
        help="Mounted guest data-volume root. Skips disk-image attach and writes directly here.",
    )
    ap.add_argument(
        "--user",
        action="append",
        dest="users",
        help="Guest username whose per-user idle/lock preferences should be tuned. Repeatable.",
    )
    ap.add_argument(
        "--skip-software-update",
        action="store_true",
        help="Do not disable automatic macOS/App Store update checks, downloads, and installs.",
    )
    ap.add_argument(
        "--keep-security-responses",
        action="store_true",
        help="Leave Security Responses/system data auto-install keys unchanged.",
    )
    ap.add_argument(
        "--skip-screen-lock",
        action="store_true",
        help="Do not write per-user screen-saver/password-delay preferences.",
    )
    ap.add_argument(
        "--skip-time-machine",
        action="store_true",
        help="Do not suppress Time Machine new-disk prompts.",
    )
    ap.add_argument(
        "--skip-spotlight",
        action="store_true",
        help="Do not create .metadata_never_index on the guest data volume.",
    )
    return ap.parse_args()


def main() -> int:
    ns = parse_args()
    try:
        if ns.mounted_root:
            return apply_tuning(
                seed.resolve_existing_path(ns.mounted_root),
                users=ns.users,
                skip_software_update=ns.skip_software_update,
                keep_security_responses=ns.keep_security_responses,
                skip_screen_lock=ns.skip_screen_lock,
                skip_time_machine=ns.skip_time_machine,
                skip_spotlight=ns.skip_spotlight,
            )

        bundle = seed.resolve_existing_path(ns.bundle)
        with seed.mounted_data_root(bundle) as data_root:
            return apply_tuning(
                data_root,
                users=ns.users,
                skip_software_update=ns.skip_software_update,
                keep_security_responses=ns.keep_security_responses,
                skip_screen_lock=ns.skip_screen_lock,
                skip_time_machine=ns.skip_time_machine,
                skip_spotlight=ns.skip_spotlight,
            )
    except seed.SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
