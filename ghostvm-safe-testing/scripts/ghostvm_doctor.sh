#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_doctor.sh --vm <Name> [--bundle /path/to/<Name>.GhostVM] [--root ~/VMs] [--snapshot clean-state] [--no-start]

Checks:
  - vmctl is available and not installed as a symlink
  - VM bundle exists
  - snapshot exists
  - config avoids known GhostVMHelper prompt/failure modes
  - (optional) VM can be started via GhostVMHelper and GhostTools responds to /health
  - (optional) remote exec works using an absolute executable path

Exit codes:
  0  OK
  1  ACTION REQUIRED (human)
USAGE
}

say() { printf '%b\n' "$*" >&2; }

action_required() {
    say ""
    say "ACTION REQUIRED (human)"
    say "$*"
    exit 1
}

VM_NAME=""
BUNDLE_PATH=""
ROOT_DIR="$HOME/VMs"
SNAPSHOT_NAME="clean-state"
DO_START=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vm)
            VM_NAME="$2"
            shift 2
            ;;
        --bundle)
            BUNDLE_PATH="$2"
            shift 2
            ;;
        --root)
            ROOT_DIR="$2"
            shift 2
            ;;
        --snapshot)
            SNAPSHOT_NAME="$2"
            shift 2
            ;;
        --no-start)
            DO_START=0
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            action_required "Unknown argument: $1 (use --help)"
            ;;
    esac
done

if [[ -z "$BUNDLE_PATH" ]]; then
    [[ -n "$VM_NAME" ]] || action_required "Missing --vm <Name> (or pass --bundle)"
    ROOT_DIR="$(
        python3 - "$ROOT_DIR" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
    )"
    BUNDLE_PATH="$ROOT_DIR/$VM_NAME.GhostVM"
fi

BUNDLE_PATH="$(
    python3 - "$BUNDLE_PATH" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
)"

VM_NAME="${VM_NAME:-$(basename "$BUNDLE_PATH" .GhostVM)}"

say "[doctor] vm=$VM_NAME"

# ---- vmctl sanity ----
if ! command -v vmctl >/dev/null 2>&1; then
    action_required "vmctl not found on PATH. Install the wrapper:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

VMCTL_PATH="$(command -v vmctl)"
if [[ -L "$VMCTL_PATH" ]]; then
    action_required "vmctl is a symlink ($VMCTL_PATH). This often breaks GhostVMHelper discovery. Reinstall using the wrapper installer:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

if ! vmctl --help >/dev/null 2>&1; then
    action_required "vmctl exists ($VMCTL_PATH) but failed to run. Ensure GhostVM.app is installed and opened once to clear Gatekeeper prompts."
fi

# ---- bundle sanity ----
if [[ ! -d "$BUNDLE_PATH" ]]; then
    action_required "VM bundle not found: $BUNDLE_PATH\nExpected default: ~/VMs/<Name>.GhostVM"
fi

CONFIG_JSON="$BUNDLE_PATH/config.json"
if [[ ! -f "$CONFIG_JSON" ]]; then
    action_required "config.json not found in VM bundle: $CONFIG_JSON"
fi

GUARD_PY="$(dirname "$0")/ghostvm_automation_guard.py"
if [[ -f "$GUARD_PY" ]]; then
    if ! python3 "$GUARD_PY" inspect --bundle "$BUNDLE_PATH" >/dev/null; then
        action_required "GhostVM config has automation-blocking settings. Re-run:
  python3 '$GUARD_PY' inspect --bundle '$BUNDLE_PATH'
Then fix missing shared-folder paths or unavailable/empty bridged networking."
    fi
fi

SNAPSHOT_DIR="$BUNDLE_PATH/Snapshots/$SNAPSHOT_NAME"
if [[ ! -d "$SNAPSHOT_DIR" ]]; then
    action_required "Snapshot '$SNAPSHOT_NAME' not found at: $SNAPSHOT_DIR\nCreate it (VM must be stopped):\n  vmctl snapshot '$BUNDLE_PATH' create '$SNAPSHOT_NAME'"
fi

say "[doctor] ok: vmctl + bundle + snapshot present"

# ---- optional runtime checks ----
if [[ $DO_START -ne 1 ]]; then
    say "[doctor] runtime checks skipped (--no-start)"
    exit 0
fi

STARTED_BY_DOCTOR=0
START_LOG="$(mktemp -t ghostvm-start.XXXXXX)"
START_PID=""

cleanup() {
    if [[ $STARTED_BY_DOCTOR -eq 1 ]]; then
        say "[doctor] stopping VM (cleanup)"
        vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
        if [[ -n "$START_PID" ]]; then
            wait "$START_PID" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$START_LOG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sock_path=""

PID_FILE="$BUNDLE_PATH/vmctl.pid"
vm_pid_alive() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(tr -d '[:space:]' <"$PID_FILE" | sed 's/^embedded://')"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    /bin/kill -0 "$pid" 2>/dev/null
}

if sock_path="$(vmctl socket "$BUNDLE_PATH" 2>/dev/null)"; then
    say "[doctor] VM appears to be running"
else
    if vm_pid_alive; then
        action_required "VM appears to be running, but the Host API socket is missing.\n\nCommon causes:\n- VM was started in --headless mode (Host API is not available)\n- GhostVMHelper failed to launch\n\nFix:\n1) Stop the VM (GhostVM GUI or: vmctl stop '$BUNDLE_PATH')\n2) Start it normally (no --headless)\n3) Re-run this doctor\n\nSee: ghostvm-safe-testing/references/troubleshooting.md"
    fi
    STARTED_BY_DOCTOR=1
    say "[doctor] starting VM via GhostVMHelper (background)"
    vmctl start "$BUNDLE_PATH" >"$START_LOG" 2>&1 &
    START_PID=$!

    # wait for socket
    deadline=$((SECONDS + 180))
    while [[ $SECONDS -lt $deadline ]]; do
        if sock_path="$(vmctl socket "$BUNDLE_PATH" 2>/dev/null)"; then
            break
        fi
        if ! /bin/kill -0 "$START_PID" 2>/dev/null; then
            # vmctl start exited early (usually helper discovery / launch failure)
            break
        fi
        sleep 1
    done

    if [[ -z "$sock_path" ]]; then
        say "[doctor] start logs:"
        sed -n '1,200p' "$START_LOG" >&2 || true
        action_required "Timed out waiting for Host API socket.\nCommon causes:\n- GhostVMHelper failed to launch (vmctl helper lookup issue)\n- GhostVM.app not installed / not opened once\nSee: ghostvm-safe-testing/references/troubleshooting.md"
    fi
fi

say "[doctor] socket: $sock_path"

# /health
if ! vmctl remote --socket "$sock_path" health >/dev/null 2>&1; then
    action_required "Host API reachable, but GhostTools /health failed.\nEnsure GhostTools is installed + running in the guest (Login Items; auto-login recommended).\nSee: ghostvm-safe-testing/references/troubleshooting.md"
fi

# exec sanity: use absolute path
say "[doctor] checking remote exec (may take a few seconds after /health)"
deadline=$((SECONDS + 30))
last_exec_err=""
while [[ $SECONDS -lt $deadline ]]; do
    set +e
    exec_out="$(vmctl remote --socket "$sock_path" exec /usr/bin/uname -a 2>&1)"
    rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
        last_exec_err=""
        break
    fi
    last_exec_err="$exec_out"
    sleep "${GHOSTVM_DOCTOR_RETRY_SLEEP:-1}"
done

if [[ -n "$last_exec_err" ]]; then
    action_required "GhostTools /health works, but remote exec failed even with an absolute executable path.\nLast error:\n$last_exec_err\nSee: ghostvm-safe-testing/references/remote-exec.md"
fi

say "[doctor] ok: remote health + exec"

if [[ $STARTED_BY_DOCTOR -eq 1 ]]; then
    say "[doctor] stopping VM (doctor started it)"
    vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
    wait "$START_PID" >/dev/null 2>&1 || true
    STARTED_BY_DOCTOR=0
fi

say "[doctor] done"
