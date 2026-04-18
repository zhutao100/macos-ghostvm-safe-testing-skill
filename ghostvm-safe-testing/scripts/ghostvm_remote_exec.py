#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run a command in a GhostVM guest via `vmctl remote --json exec`, "
            "and exit with the guest process exit code."
        )
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--socket", help="Host API unix socket path")
    g.add_argument(
        "--name",
        help="VM name (uses ~/Library/Application Support/GhostVM/api/<Name>.GhostVM.sock)",
    )
    ap.add_argument(
        "command", nargs=argparse.REMAINDER, help="Command to run (absolute path recommended)"
    )
    ns = ap.parse_args()

    if not ns.command:
        ap.error(
            "missing command; example: ghostvm_remote_exec.py --socket ... /bin/zsh -lc 'uname -a'"
        )

    cmd = ["vmctl", "remote", "--json"]
    if ns.socket:
        cmd += ["--socket", ns.socket]
    else:
        cmd += ["--name", ns.name]

    cmd += ["exec", *ns.command]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        # vmctl failures are printed on stderr already.
        sys.stderr.write(proc.stderr)
        return proc.returncode

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("error: failed to parse vmctl JSON output\n")
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return 2

    out = payload.get("stdout", "")
    err = payload.get("stderr", "")
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    if err:
        sys.stderr.write(err)
        if not err.endswith("\n"):
            sys.stderr.write("\n")

    exit_code = int(payload.get("exitCode", 0) or 0)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
