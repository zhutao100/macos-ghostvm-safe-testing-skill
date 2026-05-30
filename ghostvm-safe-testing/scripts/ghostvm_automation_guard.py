#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HELPER_DEFAULTS_DOMAIN = "org.ghostvm.ghostvm.helper"
HELPER_KEYS = {
    "autoPortMap": "bool",
    "openURLsAutomatically": "bool",
    "clipboardSyncMode": "string",
}
HELPER_APPLIED_VALUES: dict[str, bool | str] = {
    "autoPortMap": False,
    "openURLsAutomatically": False,
    "clipboardSyncMode": "disabled",
}


class GuardError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, file=sys.stderr)


def resolve_bundle(path: str) -> Path:
    # Match GhostVM/vmctl's URL(fileURLWithPath:).standardizedFileURL.path behavior:
    # expand user + normalize path components, but do not resolve symlinks.
    bundle = Path(os.path.abspath(os.path.expanduser(path)))
    if not bundle.exists() or not bundle.is_dir():
        raise GuardError(f"VM bundle not found: {bundle}")
    if bundle.suffix.lower() != ".ghostvm":
        raise GuardError(f"bundle path must end with .GhostVM: {bundle}")
    return bundle


def config_path_for(bundle: Path) -> Path:
    path = bundle / "config.json"
    if not path.exists():
        raise GuardError(f"config.json not found: {path}")
    return path


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuardError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GuardError(f"expected JSON object in {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def stable_hash(value: str) -> int:
    # GhostVMKit/Utilities/StableHash.swift: UInt64 DJB2 over UTF-8 bytes.
    h = 5381
    for b in value.encode("utf-8"):
        h = ((h << 5) + h + b) & 0xFFFFFFFFFFFFFFFF
    return h


def helper_default_keys(bundle: Path) -> dict[str, str]:
    suffix = str(stable_hash(str(bundle)))
    return {base: f"{base}_{suffix}" for base in HELPER_KEYS}


def is_darwin_defaults_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("defaults") is not None


def defaults_read(domain: str, key: str) -> dict[str, Any]:
    if not is_darwin_defaults_available():
        return {"available": False, "exists": False, "value": None}
    proc = subprocess.run(["defaults", "read", domain, key], capture_output=True, text=True)
    if proc.returncode != 0:
        return {"available": True, "exists": False, "value": None}
    return {"available": True, "exists": True, "value": proc.stdout.rstrip("\n")}


def defaults_write(domain: str, key: str, kind: str, value: bool | str) -> None:
    if not is_darwin_defaults_available():
        return
    if kind == "bool":
        raw = "true" if bool(value) else "false"
        cmd = ["defaults", "write", domain, key, "-bool", raw]
    elif kind == "string":
        cmd = ["defaults", "write", domain, key, "-string", str(value)]
    else:
        raise GuardError(f"unsupported defaults kind for {key}: {kind}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise GuardError(f"failed to write host default {domain}/{key}: {detail}")


def defaults_delete(domain: str, key: str) -> None:
    if not is_darwin_defaults_available():
        return
    subprocess.run(["defaults", "delete", domain, key], capture_output=True, text=True)


def backup_helper_defaults(bundle: Path) -> dict[str, Any]:
    keys = helper_default_keys(bundle)
    entries: dict[str, Any] = {}
    for base, actual in keys.items():
        kind = HELPER_KEYS[base]
        entry = defaults_read(HELPER_DEFAULTS_DOMAIN, actual)
        entry["base"] = base
        entry["kind"] = kind
        entries[actual] = entry
    return entries


def apply_helper_defaults(bundle: Path) -> None:
    for base, actual in helper_default_keys(bundle).items():
        defaults_write(
            HELPER_DEFAULTS_DOMAIN, actual, HELPER_KEYS[base], HELPER_APPLIED_VALUES[base]
        )


def restore_helper_defaults(entries: dict[str, Any]) -> None:
    for key, entry in entries.items():
        if not entry.get("available", True):
            continue
        kind = str(entry.get("kind", "string"))
        if entry.get("exists"):
            value = entry.get("value", "")
            if kind == "bool":
                text = str(value).strip().lower()
                defaults_write(HELPER_DEFAULTS_DOMAIN, key, kind, text in {"1", "true", "yes"})
            else:
                defaults_write(HELPER_DEFAULTS_DOMAIN, key, kind, str(value))
        else:
            defaults_delete(HELPER_DEFAULTS_DOMAIN, key)


def share_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    shared = cfg.get("sharedFolders")
    if isinstance(shared, list) and shared:
        return [entry for entry in shared if isinstance(entry, dict)]
    legacy = cfg.get("sharedFolderPath")
    if isinstance(legacy, str) and legacy.strip():
        return [
            {"path": legacy, "readOnly": cfg.get("sharedFolderReadOnly", False), "legacy": True}
        ]
    return []


def host_network_interface_ids() -> set[str] | None:
    if platform.system() != "Darwin":
        return None
    ifconfig = shutil.which("ifconfig")
    if not ifconfig and Path("/sbin/ifconfig").exists():
        ifconfig = "/sbin/ifconfig"
    if not ifconfig:
        return None
    proc = subprocess.run([ifconfig, "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return {item for item in proc.stdout.split() if item}


def inspect_config(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    leaves: dict[str, str] = {}
    for entry in share_entries(cfg):
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw.strip():
            errors.append("shared folder entry is missing a non-empty path")
            continue
        path = Path(os.path.abspath(os.path.expanduser(raw)))
        if not path.exists():
            errors.append(f"shared folder path does not exist: {path}")
        elif not path.is_dir():
            errors.append(f"shared folder path is not a directory: {path}")
        leaf = path.name
        if leaf in leaves:
            warnings.append(
                "shared folder leaf names duplicate: "
                f"{leaves[leaf]} and {path}. GhostVM disambiguates duplicate guest mount names."
            )
        else:
            leaves[leaf] = str(path)

    network = cfg.get("networkConfig")
    if isinstance(network, dict):
        mode = network.get("mode")
        bridge_id = str(network.get("bridgeInterfaceIdentifier") or "")
        if mode == "bridged" and not bridge_id:
            errors.append("networkConfig.mode is bridged but bridgeInterfaceIdentifier is empty")
        elif mode == "bridged":
            interfaces = host_network_interface_ids()
            if interfaces is not None and bridge_id not in interfaces:
                errors.append(
                    "networkConfig.mode is bridged but bridgeInterfaceIdentifier "
                    f"is not available on this host: {bridge_id}"
                )

    forwards = cfg.get("portForwards")
    if isinstance(forwards, list) and forwards:
        enabled = [f for f in forwards if not isinstance(f, dict) or f.get("enabled", True)]
        if enabled:
            warnings.append(
                f"{len(enabled)} enabled port forward(s) may conflict with host ports during automation"
            )

    legacy = cfg.get("sharedFolderPath")
    if isinstance(legacy, str) and legacy.strip() and cfg.get("sharedFolders"):
        warnings.append(
            "legacy sharedFolderPath is still set; clear it to avoid future fallback prompts"
        )

    return errors, warnings


def patch_config(
    cfg: dict[str, Any],
    *,
    force_nat: bool,
    disable_port_forwards: bool,
    clear_legacy: bool,
    clear_shared_folders: bool,
) -> dict[str, Any]:
    patched = json.loads(json.dumps(cfg))

    if force_nat:
        patched["networkConfig"] = {"mode": "nat", "bridgeInterfaceIdentifier": None}

    if disable_port_forwards:
        patched["portForwards"] = []

    if clear_shared_folders:
        patched["sharedFolders"] = []

    if clear_legacy:
        patched["sharedFolderPath"] = None
        if "sharedFolderReadOnly" in patched:
            patched["sharedFolderReadOnly"] = False

    return patched


def apply_guard(ns: argparse.Namespace) -> int:
    bundle = resolve_bundle(ns.bundle)
    config_path = config_path_for(bundle)
    state_path = Path(os.path.expanduser(ns.state)).resolve()
    if state_path.exists() and not ns.force:
        raise GuardError(f"state file already exists: {state_path}; pass --force to overwrite")

    original = load_json(config_path)
    errors, warnings = inspect_config(original)
    for warning in warnings:
        say(f"[guard] warning: {warning}")

    patched = patch_config(
        original,
        force_nat=ns.force_nat,
        disable_port_forwards=ns.disable_port_forwards,
        clear_legacy=not ns.keep_legacy_shared_folder,
        clear_shared_folders=ns.clear_shared_folders,
    )
    patched_errors, patched_warnings = inspect_config(patched)
    for warning in patched_warnings:
        if warning not in warnings:
            say(f"[guard] warning after patch: {warning}")
    fixed_errors = [error for error in errors if error not in patched_errors]
    for error in fixed_errors:
        say(f"[guard] will patch existing config issue: {error}")
    if patched_errors and not ns.allow_existing_config_issues:
        raise GuardError("config preflight failed:\n  - " + "\n  - ".join(patched_errors))

    state = {
        "schema": 1,
        "createdAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundlePath": str(bundle),
        "configPath": str(config_path),
        "originalConfig": original,
        "helperDefaultsDomain": HELPER_DEFAULTS_DOMAIN,
        "helperDefaults": backup_helper_defaults(bundle) if ns.configure_helper_defaults else {},
        "applied": {
            "forceNat": ns.force_nat,
            "disablePortForwards": ns.disable_port_forwards,
            "clearLegacySharedFolder": not ns.keep_legacy_shared_folder,
            "clearSharedFolders": ns.clear_shared_folders,
            "configureHelperDefaults": ns.configure_helper_defaults,
        },
    }

    # Persist restore state before mutating config/defaults. If a later host-side write fails,
    # agents can still restore with: ghostvm_automation_guard.py restore --state <file>.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, state)

    atomic_write_json(config_path, patched)
    if ns.configure_helper_defaults:
        apply_helper_defaults(bundle)

    say(f"[guard] saved automation state: {state_path}")
    say(f"[guard] patched config: {config_path}")
    if ns.configure_helper_defaults and not is_darwin_defaults_available():
        say("[guard] note: host defaults are only applied on macOS with /usr/bin/defaults")
    return 0


def stop_vm(bundle: Path, timeout: int) -> None:
    vmctl = shutil.which("vmctl")
    if not vmctl:
        raise GuardError("vmctl not found on PATH; cannot stop VM before restore")

    pid_file = bundle / "vmctl.pid"

    def vm_pid_alive() -> bool:
        if not pid_file.exists():
            return False
        raw = (
            pid_file.read_text(encoding="utf-8", errors="ignore").strip().removeprefix("embedded:")
        )
        if not raw.isdigit():
            return False
        try:
            os.kill(int(raw), 0)
        except OSError:
            return False
        return True

    def socket_available() -> bool:
        return (
            subprocess.run(
                [vmctl, "socket", str(bundle)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            == 0
        )

    if socket_available() or vm_pid_alive():
        say("[guard] stopping VM before restoring config")
        subprocess.run(
            [vmctl, "stop", str(bundle)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not socket_available() and not vm_pid_alive():
                return
            time.sleep(1)
        raise GuardError("timed out waiting for VM to stop before restore")


def restore_guard(ns: argparse.Namespace) -> int:
    state_path = Path(os.path.expanduser(ns.state)).resolve()
    state = load_json(state_path)
    bundle = resolve_bundle(str(state.get("bundlePath")))
    raw_config_path = str(state.get("configPath") or (bundle / "config.json"))
    config_path = Path(os.path.abspath(os.path.expanduser(raw_config_path)))

    if ns.stop_vm:
        stop_vm(bundle, ns.stop_timeout)

    if not ns.no_config:
        original = state.get("originalConfig")
        if not isinstance(original, dict):
            raise GuardError(f"state file has no originalConfig object: {state_path}")
        atomic_write_json(config_path, original)
        say(f"[guard] restored config: {config_path}")

    if not ns.no_helper_defaults:
        defaults = state.get("helperDefaults")
        if isinstance(defaults, dict) and defaults:
            restore_helper_defaults(defaults)
            if not is_darwin_defaults_available():
                say(
                    "[guard] note: host defaults restore is only applied on macOS with /usr/bin/defaults"
                )
            else:
                say(f"[guard] restored helper defaults: {HELPER_DEFAULTS_DOMAIN}")

    if ns.delete_state:
        state_path.unlink(missing_ok=True)
        say(f"[guard] deleted state file: {state_path}")

    return 0


def inspect_guard(ns: argparse.Namespace) -> int:
    bundle = resolve_bundle(ns.bundle)
    cfg = load_json(config_path_for(bundle))
    errors, warnings = inspect_config(cfg)
    if warnings:
        for warning in warnings:
            say(f"[guard] warning: {warning}")
    if errors:
        for error in errors:
            say(f"[guard] error: {error}")
        return 1
    say("[guard] config preflight OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Preflight, temporarily patch, and restore GhostVM host-side settings for unattended automation."
    )
    sub = ap.add_subparsers(dest="command", required=True)

    inspect_ap = sub.add_parser(
        "inspect", help="Validate config.json for automation-blocking settings"
    )
    inspect_ap.add_argument("--bundle", required=True, help="Path to <Name>.GhostVM bundle")
    inspect_ap.set_defaults(func=inspect_guard)

    apply_ap = sub.add_parser(
        "apply", help="Save current config/defaults and apply safe automation settings"
    )
    apply_ap.add_argument("--bundle", required=True, help="Path to <Name>.GhostVM bundle")
    apply_ap.add_argument("--state", required=True, help="Path for automation state JSON")
    apply_ap.add_argument("--force", action="store_true", help="Overwrite existing --state")
    apply_ap.add_argument(
        "--force-nat", action="store_true", help="Temporarily switch VM networking to NAT"
    )
    apply_ap.add_argument(
        "--disable-port-forwards", action="store_true", help="Temporarily clear VM portForwards"
    )
    apply_ap.add_argument(
        "--clear-shared-folders",
        action="store_true",
        help="Temporarily clear sharedFolders before run-specific shares are configured",
    )
    apply_ap.add_argument(
        "--configure-helper-defaults",
        action="store_true",
        help="Disable helper clipboard sync, auto port map, and URL auto-open defaults for this VM",
    )
    apply_ap.add_argument(
        "--keep-legacy-shared-folder",
        action="store_true",
        help="Do not clear legacy sharedFolderPath/sharedFolderReadOnly",
    )
    apply_ap.add_argument(
        "--allow-existing-config-issues",
        action="store_true",
        help="Patch despite pre-existing config issues",
    )
    apply_ap.set_defaults(func=apply_guard)

    restore_ap = sub.add_parser("restore", help="Restore a state JSON saved by apply")
    restore_ap.add_argument("--state", required=True, help="Path to automation state JSON")
    restore_ap.add_argument(
        "--stop-vm", action="store_true", help="Stop the VM before restoring config/defaults"
    )
    restore_ap.add_argument(
        "--stop-timeout", type=int, default=240, help="Seconds to wait for --stop-vm"
    )
    restore_ap.add_argument("--no-config", action="store_true", help="Do not restore config.json")
    restore_ap.add_argument(
        "--no-helper-defaults", action="store_true", help="Do not restore helper defaults"
    )
    restore_ap.add_argument(
        "--delete-state", action="store_true", help="Delete state JSON after successful restore"
    )
    restore_ap.set_defaults(func=restore_guard)

    return ap


def main() -> int:
    ns = build_parser().parse_args()
    try:
        return ns.func(ns)
    except GuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
