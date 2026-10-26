#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive, finite seconds")
    return seconds


def execute(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "vmctl request timed out; guest execution may still be running\n"
    if proc.returncode != 0:
        return proc.returncode, "", proc.stderr
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return 2, "", "error: failed to parse vmctl JSON output\n" + proc.stdout + proc.stderr
    if not isinstance(payload, dict) or type(payload.get("exitCode")) is not int:
        return 2, "", "error: guest exec response requires an integer exitCode\n"
    out = payload.get("stdout", "")
    err = payload.get("stderr", "")
    if not isinstance(out, str) or not isinstance(err, str):
        return 2, "", "error: guest stdout and stderr must be strings\n"
    return payload["exitCode"], out, err


def wait_ready(cmd: list[str], timeout: float) -> int:
    started = time.monotonic()
    deadline = started + timeout
    last_error = "no successful guest exec response"
    while (remaining := deadline - time.monotonic()) > 0:
        # Retry only this read-only probe, never an arbitrary guest operation.
        code, out, err = execute([*cmd, "exec", "/bin/echo", "ok"], min(10, remaining))
        if code == 0 and out.strip() == "ok":
            sys.stderr.write(f"guest exec ready after {time.monotonic() - started:.1f}s\n")
            return 0
        last_error = err or out or f"guest exitCode={code}; readiness acknowledgement missing"
        time.sleep(max(0, min(1, deadline - time.monotonic())))
    sys.stderr.write(f"guest exec not ready within {timeout:g}s: {last_error.rstrip()}\n")
    return 124


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
        "--timeout",
        type=positive_seconds,
        default=30,
        help="bound one host vmctl request in seconds (default: 30; not guest cancellation)",
    )
    ap.add_argument(
        "--wait-ready",
        type=positive_seconds,
        metavar="SECONDS",
        help="retry a fixed read-only guest probe within this deadline; no command allowed",
    )
    ap.add_argument(
        "command", nargs=argparse.REMAINDER, help="Command to run (absolute path recommended)"
    )
    ns = ap.parse_args()

    if ns.wait_ready is not None and ns.command:
        ap.error("--wait-ready does not accept a command")
    if ns.wait_ready is None and not ns.command:
        ap.error(
            "missing command; example: ghostvm_remote_exec.py --socket ... /bin/zsh -lc 'uname -a'"
        )

    cmd = ["vmctl", "remote", "--json"]
    if ns.socket:
        cmd += ["--socket", ns.socket]
    else:
        cmd += ["--name", ns.name]

    if ns.wait_ready is not None:
        return wait_ready(cmd, ns.wait_ready)
    code, out, err = execute([*cmd, "exec", *ns.command], ns.timeout)
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    if err:
        sys.stderr.write(err)
        if not err.endswith("\n"):
            sys.stderr.write("\n")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
