#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_exec.sh --vm <Name> [--timeout <seconds>] -- <shell-command>
  ghostvm_exec.sh --socket <path> [--timeout <seconds>] -- <shell-command>

Executes a command inside the guest via the GhostVM Host API socket.

Implementation detail:
- Uses /bin/bash -lc "<shell-command>" inside the guest.
- Sends POST /api/v1/exec with a configurable timeout.

Return:
- prints guest stdout to stdout
- prints guest stderr to stderr
- exits with the guest exit code
USAGE
}

VM_NAME=""
SOCKET_PATH=""
TIMEOUT=30

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vm)
            VM_NAME="$2"
            shift 2
            ;;
        --socket)
            SOCKET_PATH="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$SOCKET_PATH" ]]; then
    if [[ -z "$VM_NAME" ]]; then
        usage
        exit 2
    fi
    SOCKET_PATH="$HOME/Library/Application Support/GhostVM/api/$VM_NAME.GhostVM.sock"
fi

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required to build/parse JSON for Host API exec." >&2
    echo "ACTION REQUIRED (human): install python3 (Homebrew or Xcode toolchain) or rewrite this script to avoid it." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required to call the Host API over a unix socket." >&2
    echo "ACTION REQUIRED (human): ensure /usr/bin/curl is available." >&2
    exit 1
fi

CMD="$*"

JSON_BODY="$(
    python3 - <<PY
import json
import sys
cmd = sys.argv[1]
body = {
  "command": "/bin/bash",
  "args": ["-lc", cmd],
  "timeout": int(sys.argv[2]),
}
print(json.dumps(body))
PY
    "$CMD" "$TIMEOUT"
)"

BODY_FILE="$(mktemp -t ghostvm-exec.body.XXXXXX)"
REQ_FILE="$(mktemp -t ghostvm-exec.req.XXXXXX)"
trap 'rm -f "$BODY_FILE" "$REQ_FILE"' EXIT
printf '%s' "$JSON_BODY" >"$REQ_FILE"

HTTP_CODE="$(
    curl -sS --unix-socket "$SOCKET_PATH" \
        -o "$BODY_FILE" \
        -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        --data-binary "@$REQ_FILE" \
        http://localhost/api/v1/exec
)"

python3 - "$HTTP_CODE" "$BODY_FILE" <<'PY'
import json
import pathlib
import sys

status = int(sys.argv[1])
body_path = pathlib.Path(sys.argv[2])
raw = body_path.read_text(encoding="utf-8", errors="replace")

def die(msg: str, *, code: int) -> None:
    sys.stderr.write(msg)
    if not msg.endswith("\n"):
        sys.stderr.write("\n")
    raise SystemExit(code)

try:
    payload = json.loads(raw) if raw else {}
except Exception:
    if status == 408:
        die(f"ERROR: guest exec timed out (HTTP {status}); non-JSON body: {raw}", code=124)
    die(f"ERROR: Host API returned non-JSON (HTTP {status}): {raw}", code=1)

if status != 200:
    err = payload.get("error") or raw or "unknown error"
    if status == 408:
        die(f"ERROR: guest exec timed out (HTTP {status}): {err}", code=124)
    die(f"ERROR: Host API exec failed (HTTP {status}): {err}", code=1)

stdout = payload.get("stdout", "") or ""
stderr = payload.get("stderr", "") or ""
exit_code = int(payload.get("exitCode", 0) or 0)

if stdout:
    sys.stdout.write(stdout)
    if not stdout.endswith("\n"):
        sys.stdout.write("\n")
if stderr:
    sys.stderr.write(stderr)
    if not stderr.endswith("\n"):
        sys.stderr.write("\n")

raise SystemExit(exit_code)
PY
