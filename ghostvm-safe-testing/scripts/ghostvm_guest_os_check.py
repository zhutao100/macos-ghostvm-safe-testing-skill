#!/usr/bin/env python3
"""Verify a running guest's OS and usable memory, not cached install metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from ghostvm_remote_exec import execute

PROBE = """set -e
/usr/bin/sw_vers
/usr/sbin/sysctl hw.memsize
/usr/sbin/sysctl hw.memsize_usable 2>/dev/null || true
"""


def version(value: str) -> str:
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        raise argparse.ArgumentTypeError("use a macOS version such as 26.7.2")
    return value


def memory_fraction(value: str) -> float:
    fraction = float(value)
    if not 0 < fraction <= 1:
        raise argparse.ArgumentTypeError("memory fraction must be in (0, 1]")
    return fraction


def assess(
    output: str,
    *,
    expected_version: str | None,
    expected_build: str | None,
    configured_memory: int,
    minimum_fraction: float,
) -> dict[str, object]:
    values = dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
    values = {key.strip(): value.strip() for key, value in values.items()}
    observed_version = values.get("ProductVersion")
    observed_build = values.get("BuildVersion")
    errors = []
    warnings = []
    memory = {}
    for key in ("hw.memsize", "hw.memsize_usable"):
        value = values.get(key)
        if value is not None:
            if not value.isdecimal() or int(value) <= 0:
                errors.append(f"invalid {key}: {value!r}")
            else:
                memory[key] = int(value)
    if not observed_version or not observed_build:
        errors.append("guest sw_vers did not return a version and build")
    if expected_version is not None and observed_version != expected_version:
        errors.append(f"expected macOS {expected_version}; observed {observed_version}")
    if expected_build is not None and observed_build != expected_build:
        errors.append(f"expected build {expected_build}; observed {observed_build}")
    if "hw.memsize" not in memory:
        errors.append("guest did not report hw.memsize")
    elif memory["hw.memsize"] != configured_memory:
        errors.append("guest hw.memsize differs from configured memoryBytes")
    usable = memory.get("hw.memsize_usable")
    if usable is not None:
        if usable < configured_memory * minimum_fraction:
            errors.append(
                f"usable memory {usable} is below {minimum_fraction:.0%} "
                f"of configured memory {configured_memory}"
            )
        if usable > memory.get("hw.memsize", configured_memory):
            errors.append("usable memory exceeds reported physical memory")
    elif "hw.memsize_usable" not in values:
        warnings.append("hw.memsize_usable unavailable; usable-memory check not established")
    return {
        "macos": observed_version,
        "build": observed_build,
        "configured_memory_bytes": configured_memory,
        "memory_bytes": memory.get("hw.memsize"),
        "usable_memory_bytes": usable,
        "minimum_usable_fraction": minimum_fraction,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="Running .GhostVM bundle")
    parser.add_argument("--expect-version", type=version, help="Exact guest ProductVersion")
    parser.add_argument("--expect-build", help="Exact guest BuildVersion")
    parser.add_argument(
        "--minimum-usable-fraction",
        type=memory_fraction,
        default=0.8,
        help="Minimum usable/configured memory ratio (default: 0.8; diagnostic policy)",
    )
    args = parser.parse_args()
    try:
        bundle = args.bundle.expanduser().resolve(strict=True)
        config = json.loads((bundle / "config.json").read_text())
        if not isinstance(config, dict):
            raise ValueError("config.json requires an object")
        configured_memory = config.get("memoryBytes")
        if type(configured_memory) is not int or configured_memory <= 0:
            raise ValueError("config.json requires a positive integer memoryBytes")
        socket = subprocess.run(
            ["vmctl", "socket", str(bundle)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if not socket:
            raise ValueError("vmctl did not return a Host API socket")
        code, output, error = execute(
            ["vmctl", "remote", "--json", "--socket", socket, "exec", "/bin/sh", "-c", PROBE],
            timeout=30,
        )
        if code:
            raise ValueError(f"guest OS probe failed ({code}): {error or output}")
        result = assess(
            output,
            expected_version=args.expect_version,
            expected_build=args.expect_build,
            configured_memory=configured_memory,
            minimum_fraction=args.minimum_usable_fraction,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ACTION REQUIRED (human): {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        print(
            "ACTION REQUIRED (human): guest OS/memory checks failed; "
            "retain installer logs and do not publish this snapshot.",
            file=sys.stderr,
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
