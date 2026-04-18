#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    id: str
    ok: bool
    detail: str


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_config(path: Path) -> dict[str, object]:
    try:
        return json.loads(_read_text(path))
    except FileNotFoundError:
        raise SystemExit(
            f"error: config not found: {path}\n"
            "hint: copy assets/ghostvm_source_repo.config.json.example to assets/ghostvm_source_repo.config.json and edit it"
        )
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: invalid JSON in {path}: {e}")


def _find_repo_root(start: Path) -> Path:
    # Walk up until we find a plausible repo root.
    cur = start
    while True:
        if (cur / "AGENTS.md").is_file() and (cur / "ghostvm-safe-testing").is_dir():
            return cur
        if cur.parent == cur:
            raise SystemExit(
                "error: failed to locate repo root (expected to find AGENTS.md and ghostvm-safe-testing/ above this script)"
            )
        cur = cur.parent


def _ghostvm_repo_path(update_skill_root: Path, config_rel: str) -> Path:
    cfg_path = (update_skill_root / config_rel).resolve()
    cfg = _load_config(cfg_path)
    raw = cfg.get("ghostvm_repo_path")
    if not isinstance(raw, str) or not raw.strip():
        raise SystemExit(
            f"error: {cfg_path} must contain a non-empty string field 'ghostvm_repo_path'"
        )
    return Path(os.path.expanduser(raw)).resolve()


def _check_file_contains(path: Path, pattern: str, *, flags: int = 0) -> bool:
    return re.search(pattern, _read_text(path), flags) is not None


def _pos(text: str, needle: str) -> int:
    return text.find(needle)


def _run_checks(*, ghostvm_repo: Path, skill_repo_root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []

    def add(id: str, ok: bool, detail: str) -> None:
        results.append(CheckResult(id=id, ok=ok, detail=detail))

    # ---- GhostVM source-of-truth checks ----
    cli_swift = ghostvm_repo / "macOS" / "GhostVM" / "vmctl" / "CLI.swift"
    remote_swift = ghostvm_repo / "macOS" / "GhostVM" / "vmctl" / "RemoteCommand.swift"
    router_swift = (
        ghostvm_repo / "macOS" / "GhostTools" / "Sources" / "GhostTools" / "Server" / "Router.swift"
    )
    vmcontroller_swift = ghostvm_repo / "macOS" / "GhostVMKit" / "Operations" / "VMController.swift"

    for p in [cli_swift, remote_swift, router_swift, vmcontroller_swift]:
        if not p.is_file():
            add(
                id=f"ghostvm.file_present:{p.name}",
                ok=False,
                detail=f"missing expected GhostVM source file: {p}",
            )

    if cli_swift.is_file():
        ok = _check_file_contains(cli_swift, r"CommandLine\.arguments\[0\]")
        add(
            id="ghostvm.vmctl_argv0_relative",
            ok=ok,
            detail=(
                "vmctl helper discovery is argv0-relative (findHelperApp uses CommandLine.arguments[0])"
                if ok
                else "expected vmctl findHelperApp() to reference CommandLine.arguments[0]"
            ),
        )

    if remote_swift.is_file():
        ok = _check_file_contains(remote_swift, r"GhostVM/api/\(.*\)\.GhostVM\.sock")
        add(
            id="ghostvm.socket_path",
            ok=ok,
            detail=(
                "Host API socket path uses ~/Library/Application Support/GhostVM/api/<name>.GhostVM.sock"
                if ok
                else "expected vmctl remote to construct socket under Application Support/GhostVM/api/<name>.GhostVM.sock"
            ),
        )

    if router_swift.is_file():
        ok_abs = _check_file_contains(
            router_swift,
            r"process\.executableURL\s*=\s*URL\(fileURLWithPath:\s*payload\.command\)",
        )
        add(
            id="ghostvm.exec_requires_absolute_path",
            ok=ok_abs,
            detail=(
                "GhostTools /api/v1/exec launches Process(executableURL: fileURLWithPath(payload.command))"
                if ok_abs
                else "expected GhostTools exec to use Process.executableURL = URL(fileURLWithPath: payload.command)"
            ),
        )

        ok_timeout = _check_file_contains(router_swift, r"payload\.timeout\s*\?\?\s*30")
        add(
            id="ghostvm.exec_timeout_default_30",
            ok=ok_timeout,
            detail=(
                "GhostTools exec timeout defaults to 30s when omitted"
                if ok_timeout
                else "expected GhostTools exec to default timeout via payload.timeout ?? 30"
            ),
        )

    if vmcontroller_swift.is_file():
        ok = _check_file_contains(vmcontroller_swift, r"\(\"config\.json\",\s*layout\.configURL\)")
        add(
            id="ghostvm.snapshots_include_config_json",
            ok=ok,
            detail=(
                'Snapshots include config.json (VMController itemsToCopy contains ("config.json", layout.configURL))'
                if ok
                else "expected snapshot itemsToCopy to include config.json"
            ),
        )

    # ---- Skill repo conformance checks ----
    skill_md = skill_repo_root / "ghostvm-safe-testing" / "SKILL.md"
    safe_test_sh = skill_repo_root / "ghostvm-safe-testing" / "scripts" / "ghostvm_safe_test.sh"

    if skill_md.is_file():
        ok = "Snapshots include `config.json`" in _read_text(skill_md)
        add(
            id="skill.notes_snapshots_include_config",
            ok=ok,
            detail=(
                "Skill documents that snapshots include config.json"
                if ok
                else "expected ghostvm-safe-testing/SKILL.md to mention that snapshots include config.json"
            ),
        )
    else:
        add(
            id="skill.file_present:SKILL.md",
            ok=False,
            detail=f"missing expected file: {skill_md}",
        )

    if safe_test_sh.is_file():
        t = _read_text(safe_test_sh)
        i_revert = _pos(t, "[runner] reverting snapshot")
        i_cfg = _pos(t, "[runner] configuring shared folders")
        ok = i_revert != -1 and i_cfg != -1 and i_revert < i_cfg
        add(
            id="skill.safe_runner_ordering",
            ok=ok,
            detail=(
                "Safe runner reverts snapshot before editing config.json"
                if ok
                else "expected ghostvm_safe_test.sh to revert snapshot before editing config.json"
            ),
        )
    else:
        add(
            id="skill.file_present:ghostvm_safe_test.sh",
            ok=False,
            detail=f"missing expected file: {safe_test_sh}",
        )

    return results


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Validate that this repo's GhostVM safe-testing skill matches key behaviors in a local GhostVM source checkout."
        )
    )
    ap.add_argument(
        "--config",
        default="assets/ghostvm_source_repo.config.json",
        help=(
            'Path (relative to this skill root) to a JSON config with {"ghostvm_repo_path": "..."}. '
            "Default: assets/ghostvm_source_repo.config.json"
        ),
    )
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ns = ap.parse_args()

    update_skill_root = Path(__file__).resolve().parents[1]
    repo_root = _find_repo_root(update_skill_root)
    ghostvm_repo = _ghostvm_repo_path(update_skill_root, ns.config)

    if not ghostvm_repo.is_dir():
        raise SystemExit(
            f"error: GhostVM repo path does not exist or is not a directory: {ghostvm_repo}"
        )

    results = _run_checks(ghostvm_repo=ghostvm_repo, skill_repo_root=repo_root)

    ok = all(r.ok for r in results)

    if ns.json:
        payload = {
            "ok": ok,
            "ghostvm_repo": str(ghostvm_repo),
            "checks": [r.__dict__ for r in results],
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return 0 if ok else 1

    for r in results:
        prefix = "OK" if r.ok else "FAIL"
        print(f"[{prefix}] {r.id}: {r.detail}")

    if ok:
        print("\nAll checks passed.")
        return 0

    print(
        "\nOne or more checks failed. Review failures, update the skill, then re-run this verifier."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
