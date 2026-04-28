#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _abs_dir(p: str) -> Path:
    path = Path(os.path.expanduser(p)).resolve()
    if not path.exists():
        raise SystemExit(f"error: path does not exist: {path}")
    if not path.is_dir():
        raise SystemExit(f"error: path is not a directory: {path}")
    return path


def _bundle_path(vm: str | None, bundle: str | None, root: str | None) -> Path:
    if bundle:
        b = Path(os.path.expanduser(bundle)).resolve()
        return b

    if not vm:
        raise SystemExit("error: must pass --vm <Name> or --bundle <path>")

    root_dir = Path(os.path.expanduser(root or "~/VMs")).resolve()
    return (root_dir / f"{vm}.GhostVM").resolve()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: config not found: {path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: invalid JSON in {path}: {e}")


def _write_json(path: Path, obj: dict, *, dry_run: bool) -> None:
    data = json.dumps(obj, indent=2, sort_keys=True)
    data += "\n"
    if dry_run:
        sys.stdout.write(data)
        return

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Configure GhostVM shared folders (RO + RW) by editing <bundle>/config.json",
    )
    ap.add_argument("--vm", help="VM name (bundle is ~/VMs/<Name>.GhostVM unless --root is set)")
    ap.add_argument("--bundle", help="Absolute path to a .GhostVM bundle")
    ap.add_argument(
        "--root", help="Root directory that contains <Name>.GhostVM bundles (default: ~/VMs)"
    )
    ap.add_argument("--ro", required=True, help="Host directory to expose read-only")
    ap.add_argument("--rw", required=True, help="Host directory to expose writable")
    ap.add_argument(
        "--dry-run", action="store_true", help="Print updated config.json to stdout, do not write"
    )
    ns = ap.parse_args()

    bundle = _bundle_path(ns.vm, ns.bundle, ns.root)
    if not bundle.exists() or not bundle.is_dir():
        raise SystemExit(f"error: VM bundle not found: {bundle}")
    if bundle.suffix.lower() != ".ghostvm":
        raise SystemExit(f"error: bundle path must end with .GhostVM: {bundle}")

    ro = _abs_dir(ns.ro)
    rw = _abs_dir(ns.rw)

    if ro.name == rw.name:
        raise SystemExit(
            "error: --ro and --rw must have different leaf directory names "
            "(GhostVM uses the leaf name as the share name)"
        )

    config_path = bundle / "config.json"
    cfg = _load_json(config_path)

    def _mk_entry(path: Path, read_only: bool) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "path": str(path),
            "readOnly": read_only,
        }

    cfg["sharedFolders"] = [
        _mk_entry(ro, True),
        _mk_entry(rw, False),
    ]

    # Match GhostVM’s config format: ISO-8601 timestamps in UTC.
    # (GhostVM writes modifiedAt on save; updating it here helps humans debug.)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    prev = cfg.get("modifiedAt")
    if isinstance(prev, (int, float)):
        cfg["modifiedAt"] = int(now.timestamp())
    else:
        cfg["modifiedAt"] = now.isoformat().replace("+00:00", "Z")

    # Keep legacy config from forcing every share read-only on GhostVM builds
    # that still consult sharedFolderReadOnly alongside sharedFolders.
    if "sharedFolderReadOnly" in cfg:
        cfg["sharedFolderReadOnly"] = False

    _write_json(config_path, cfg, dry_run=ns.dry_run)

    if not ns.dry_run:
        print(f"ok: updated sharedFolders in {config_path}")
        print(f"  RO: {ro} (guest: <AppleVirtIOFS mount>/{ro.name})")
        print(f"  RW: {rw} (guest: <AppleVirtIOFS mount>/{rw.name})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
