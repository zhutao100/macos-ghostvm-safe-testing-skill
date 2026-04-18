from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
INSTALL_WRAPPER_SH = SCRIPTS_DIR / "install_vmctl_wrapper.sh"
DOCTOR_SH = SCRIPTS_DIR / "ghostvm_doctor.sh"
EXEC_SH = SCRIPTS_DIR / "ghostvm_exec.sh"


class TestShellScriptSmoke(unittest.TestCase):
    def test_install_vmctl_wrapper_creates_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            ghostvm_app = root / "GhostVM.app"
            embedded_vmctl = (
                ghostvm_app
                / "Contents"
                / "PlugIns"
                / "Helpers"
                / "vmctl.app"
                / "Contents"
                / "MacOS"
                / "vmctl"
            )
            embedded_vmctl.parent.mkdir(parents=True, exist_ok=True)
            embedded_vmctl.write_text(
                "#!/usr/bin/env bash\n" "set -euo pipefail\n" 'echo "argv0=$0" >&2\n' "exit 0\n",
                encoding="utf-8",
            )
            embedded_vmctl.chmod(0o755)

            dest_dir = root / "bin"
            dest_dir.mkdir()

            proc = subprocess.run(
                [
                    "bash",
                    str(INSTALL_WRAPPER_SH),
                    "--ghostvm-app",
                    str(ghostvm_app),
                    "--dest",
                    str(dest_dir),
                    "--force",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            wrapper = dest_dir / "vmctl"
            self.assertTrue(wrapper.exists(), msg="expected wrapper to be created")
            self.assertFalse(wrapper.is_symlink(), msg="wrapper must not be a symlink")

            mode = wrapper.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, msg="wrapper must be executable")

            # The wrapper should be runnable and should exec the embedded vmctl by absolute path.
            env = dict(os.environ)
            env["PATH"] = f"{dest_dir}{os.pathsep}{env.get('PATH', '')}"
            proc2 = subprocess.run(
                ["vmctl", "--help"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc2.returncode, 0, msg=proc2.stderr)
            self.assertIn(str(embedded_vmctl), proc2.stderr)

    def test_doctor_runs_without_python_argv_crash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Minimal fake VM bundle with config + snapshot dir.
            bundle = root / "Dev.GhostVM"
            bundle.mkdir()
            (bundle / "config.json").write_text("{}\n", encoding="utf-8")
            (bundle / "Snapshots" / "clean-state").mkdir(parents=True)

            # Provide a fake non-symlink `vmctl` on PATH.
            bin_dir = root / "fake-bin"
            bin_dir.mkdir()
            vmctl = bin_dir / "vmctl"
            vmctl.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [[ ${1:-} == --help ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            vmctl.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

            proc = subprocess.run(
                [
                    "bash",
                    str(DOCTOR_SH),
                    "--bundle",
                    str(bundle),
                    "--snapshot",
                    "clean-state",
                    "--no-start",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_exec_builds_json_shell_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            capture = root / "req.json"

            bin_dir = root / "fake-bin"
            bin_dir.mkdir()
            curl = bin_dir / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "CAPTURE_PATH=${GHOSTVM_TEST_CAPTURE_REQ:?}\n"
                "body_file=''\n"
                "req_file=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                '  case "$1" in\n'
                '    -o) body_file="$2"; shift 2 ;;\n'
                "    --data-binary)\n"
                '      v="$2"; shift 2\n'
                '      req_file="${v#@}"\n'
                "      ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                'cp "$req_file" "$CAPTURE_PATH"\n'
                'printf \'%s\' \'{"stdout":"ok","stderr":"","exitCode":0}\' >"$body_file"\n'
                "printf '200'\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
            env["GHOSTVM_TEST_CAPTURE_REQ"] = str(capture)

            proc = subprocess.run(
                [
                    "bash",
                    str(EXEC_SH),
                    "--socket",
                    "/tmp/dev.sock",
                    "--timeout",
                    "123",
                    "--",
                    "echo",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            req = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(req["command"], "/bin/bash")
            self.assertEqual(req["timeout"], 123)
            self.assertEqual(req["args"][:2], ["-lc", "echo hello"])

    def test_exec_builds_json_argv_mode_preserves_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            capture = root / "req.json"

            bin_dir = root / "fake-bin"
            bin_dir.mkdir()
            curl = bin_dir / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "CAPTURE_PATH=${GHOSTVM_TEST_CAPTURE_REQ:?}\n"
                "body_file=''\n"
                "req_file=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                '  case "$1" in\n'
                '    -o) body_file="$2"; shift 2 ;;\n'
                "    --data-binary)\n"
                '      v="$2"; shift 2\n'
                '      req_file="${v#@}"\n'
                "      ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                'cp "$req_file" "$CAPTURE_PATH"\n'
                'printf \'%s\' \'{"stdout":"ok","stderr":"","exitCode":0}\' >"$body_file"\n'
                "printf '200'\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
            env["GHOSTVM_TEST_CAPTURE_REQ"] = str(capture)

            proc = subprocess.run(
                [
                    "bash",
                    str(EXEC_SH),
                    "--socket",
                    "/tmp/dev.sock",
                    "--timeout",
                    "45",
                    "--argv",
                    "/Volumes/My Shared Files/guest_run.sh",
                    "--flag",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            req = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(req["command"], "/bin/bash")
            self.assertEqual(req["timeout"], 45)
            self.assertEqual(
                req["args"][:4],
                ["-lc", 'exec "$@"', "_", "/Volumes/My Shared Files/guest_run.sh"],
            )
            self.assertIn("--flag", req["args"])


if __name__ == "__main__":
    unittest.main()
