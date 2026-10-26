#!/usr/bin/env python3
"""Run host-only skill checks with bounded output; native checks belong in a VM."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "ghostvm-safe-testing"


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    log_dir = Path(tempfile.mkdtemp(prefix="ghostvm-skill-check-"))
    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(log_dir / "bytecode"))
    checks = [(p.name, ["bash", "-n", str(p)]) for p in sorted((SKILL / "scripts").glob("*.sh"))]
    python_files = [*sorted((SKILL / "scripts").glob("*.py")), Path(__file__)]
    checks.append(("python-syntax", [sys.executable, "-m", "py_compile", *map(str, python_files)]))
    checks.append(
        (
            "unit-tests",
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(SKILL / "tests"),
                "-p",
                "test_*.py",
                "-q",
            ],
        )
    )
    for label, command in checks:
        try:
            proc = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            print(f"FAIL {label}; exceeded 300 seconds", file=sys.stderr)
            return 124
        output = proc.stdout + proc.stderr
        if proc.returncode:
            log = log_dir / f"{label}.log"
            log.write_text(output)
            print(f"FAIL {label}; full log: {log}", file=sys.stderr)
            print("\n".join(output.splitlines()[-30:]), file=sys.stderr)
            return proc.returncode
        if label == "unit-tests":
            match = re.search(r"Ran (\d+) tests?", output)
            print(f"Syntax checks passed; {match.group(1) if match else 'all'} tests passed.")
    shutil.rmtree(log_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
