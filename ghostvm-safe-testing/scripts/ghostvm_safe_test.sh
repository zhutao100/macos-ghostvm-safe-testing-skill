#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_safe_test.sh \
    --vm <Name> | --bundle /path/to/<Name>.GhostVM \
    --ro /absolute/host/input \
    --rw /absolute/host/output \
    [--snapshot clean-state] \
    [--timeout <seconds>] \
    --cmd '<command to run inside guest>' \
    [--keep-running]

Workflow:
  1) stop VM (if running)
  2) revert snapshot
  3) configure shared folders (RO + RW) by editing <bundle>/config.json
  4) start VM via GhostVMHelper (background)
  5) wait for Host API socket + GhostTools health
  6) validate RO is actually read-only and RW is writable
  7) copy RO input into guest-local workspace (/Users/Shared/...)
  8) run your command in that workspace
  9) write logs + optional git patch into RW output directory
  10) stop VM unless --keep-running

Outputs:
  <rw>/ghostvm-runs/<Name>/<run-id>/

Notes:
  - `vmctl remote exec` requires an absolute executable path. This script uses /bin/zsh -lc ...
  - `vmctl remote exec` uses GhostTools' default exec timeout (30s). This script uses Host API exec with an explicit timeout for the long-running guest step.
  - Prefer --keep-running when follow-up guest commands or inspection are likely, then stop the VM explicitly when done.
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
RO_PATH=""
RW_PATH=""
CMD_STR=""
KEEP_RUNNING=0
CMD_TIMEOUT="${GHOSTVM_CMD_TIMEOUT:-3600}"

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
        --ro)
            RO_PATH="$2"
            shift 2
            ;;
        --rw)
            RW_PATH="$2"
            shift 2
            ;;
        --cmd)
            CMD_STR="$2"
            shift 2
            ;;
        --timeout | --cmd-timeout)
            CMD_TIMEOUT="$2"
            shift 2
            ;;
        --keep-running)
            KEEP_RUNNING=1
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

[[ -n "$RO_PATH" ]] || action_required "Missing --ro /absolute/host/input"
[[ -n "$RW_PATH" ]] || action_required "Missing --rw /absolute/host/output"
[[ -n "$CMD_STR" ]] || action_required "Missing --cmd '<command>'"

if ! [[ "$CMD_TIMEOUT" =~ ^[0-9]+$ ]] || [[ "$CMD_TIMEOUT" -le 0 ]]; then
    action_required "--timeout must be a positive integer (seconds)"
fi

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

if ! command -v vmctl >/dev/null 2>&1; then
    action_required "vmctl not found on PATH. Install the wrapper:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

VMCTL_PATH="$(command -v vmctl)"
if [[ -L "$VMCTL_PATH" ]]; then
    action_required "vmctl is a symlink ($VMCTL_PATH). Reinstall using the wrapper installer:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

if [[ ! -d "$BUNDLE_PATH" ]]; then
    action_required "VM bundle not found: $BUNDLE_PATH"
fi

# Normalize RO/RW.
RO_ABS="$(
    python3 - "$RO_PATH" <<'PY'
import os
import sys

p = os.path.abspath(os.path.expanduser(sys.argv[1]))
print(p)
PY
)"
RW_ABS="$(
    python3 - "$RW_PATH" <<'PY'
import os
import sys

p = os.path.abspath(os.path.expanduser(sys.argv[1]))
print(p)
PY
)"

if [[ ! -d "$RO_ABS" ]]; then
    action_required "--ro is not a directory: $RO_ABS"
fi

mkdir -p "$RW_ABS" 2>/dev/null || true
if [[ ! -d "$RW_ABS" ]]; then
    action_required "--rw is not a directory and could not be created: $RW_ABS"
fi

RO_NAME="$(basename "$RO_ABS")"
RW_NAME="$(basename "$RW_ABS")"
if [[ "$RO_NAME" == "$RW_NAME" ]]; then
    action_required "--ro and --rw must have different leaf directory names (GhostVM uses the leaf name as the share name).\nExample:\n  --ro /Users/me/src/my-repo\n  --rw /Users/me/.ghostvm-artifacts"
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
HOST_RUN_DIR="$RW_ABS/ghostvm-runs/$VM_NAME/$RUN_ID"
mkdir -p "$HOST_RUN_DIR"
printf '%s' "$CMD_STR" >"$HOST_RUN_DIR/cmd.txt"

say "[runner] vm=$VM_NAME"
say "[runner] bundle=$BUNDLE_PATH"
say "[runner] snapshot=$SNAPSHOT_NAME"
say "[runner] ro=$RO_ABS (guest leaf: $RO_NAME)"
say "[runner] rw=$RW_ABS (guest leaf: $RW_NAME)"
say "[runner] run=$HOST_RUN_DIR"
say "[runner] timeout=${CMD_TIMEOUT}s"

# Stop VM if running (even if the Host API socket is missing).
PID_FILE="$BUNDLE_PATH/vmctl.pid"
vm_pid_alive() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(tr -d '[:space:]' <"$PID_FILE" | sed 's/^embedded://')"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    /bin/kill -0 "$pid" 2>/dev/null
}

if vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 || vm_pid_alive; then
    say "[runner] stopping running VM"
    vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
    deadline=$((SECONDS + 180))
    while [[ $SECONDS -lt $deadline ]]; do
        if ! vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 && ! vm_pid_alive; then
            break
        fi
        sleep 1
    done
    if vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 || vm_pid_alive; then
        action_required "Timed out waiting for VM to stop. Stop it in GhostVM GUI and retry."
    fi
fi

# Revert snapshot.
say "[runner] reverting snapshot"
if ! vmctl snapshot "$BUNDLE_PATH" revert "$SNAPSHOT_NAME" >/dev/null 2>&1; then
    action_required "Failed to revert snapshot '$SNAPSHOT_NAME'. Ensure it exists and the VM is stopped."
fi

# Configure shared folders (edits config.json).
# Note: snapshots include config.json, so configure shares AFTER revert.
say "[runner] configuring shared folders"
python3 "$(dirname "$0")/ghostvm_configure_shares.py" \
    --bundle "$BUNDLE_PATH" \
    --ro "$RO_ABS" \
    --rw "$RW_ABS" >/dev/null

# Start VM via helper in background so we can keep running commands.
START_LOG="$HOST_RUN_DIR/vmctl_start.log"
say "[runner] starting VM via GhostVMHelper (background)"
vmctl start "$BUNDLE_PATH" >"$START_LOG" 2>&1 &
START_PID=$!
STARTED_BY_RUNNER=1

cleanup() {
    if [[ $STARTED_BY_RUNNER -eq 1 && $KEEP_RUNNING -ne 1 ]]; then
        say "[runner] cleanup: stopping VM"
        vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
        wait "$START_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# Wait for socket.
sock_path=""
deadline=$((SECONDS + 240))
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
    say "[runner] start logs (first 200 lines):"
    sed -n '1,200p' "$START_LOG" >&2 || true
    action_required "Timed out waiting for Host API socket. See $START_LOG and references/troubleshooting.md"
fi

say "[runner] socket=$sock_path"

# Health check. The Host API socket can appear before the logged-in user's
# GhostTools instance is ready, especially immediately after snapshot revert.
say "[runner] waiting for GhostTools /health"
health_ok=0
last_health_err=""
deadline=$((SECONDS + 240))
while [[ $SECONDS -lt $deadline ]]; do
    if vmctl remote --socket "$sock_path" health >/dev/null 2>&1; then
        health_ok=1
        break
    fi
    last_health_err="$(vmctl remote --socket "$sock_path" health 2>&1 || true)"
    sleep 2
done
if [[ $health_ok -ne 1 ]]; then
    action_required "GhostTools /health failed after waiting for guest login. Ensure GhostTools is installed + running in the guest. Last error:\n$last_health_err\nSee references/troubleshooting.md"
fi

# Validate remote exec is ready (can transiently fail right after /health).
REMOTE_EXEC_PY="$(dirname "$0")/ghostvm_remote_exec.py"
say "[runner] waiting for guest exec to be ready"
deadline=$((SECONDS + 30))
while [[ $SECONDS -lt $deadline ]]; do
    if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/echo ok >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/echo ok >/dev/null 2>&1; then
    action_required "Guest exec is not responding yet (even though /health is OK).\nFix: wait for the user session to finish logging in and for GhostTools to fully initialize, then retry.\nSee: references/remote-exec.md"
fi

# Discover the AppleVirtIOFS mountpoint that hosts shared folders, and wait for
# the expected share directories to appear (guest auto-mount can lag /health).
say "[runner] waiting for shared folders to mount"

MOUNT_LINE=""
SHARE_ROOT=""
GUEST_RO=""
GUEST_RW=""

deadline=$((SECONDS + 120))
while [[ $SECONDS -lt $deadline ]]; do
    MOUNT_LINE="$(
        python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/sbin/mount | /usr/bin/grep -m 1 AppleVirtIOFS" 2>/dev/null || true
    )"
    MOUNT_LINE="${MOUNT_LINE%$'\n'}"
    MOUNT_LINE="${MOUNT_LINE%$'\r'}"

    if [[ -z "$MOUNT_LINE" ]]; then
        sleep 1
        continue
    fi

    SHARE_ROOT="${MOUNT_LINE#* on }"
    SHARE_ROOT="${SHARE_ROOT%% (*}"
    if [[ -z "$SHARE_ROOT" || "$SHARE_ROOT" == "$MOUNT_LINE" ]]; then
        sleep 1
        continue
    fi

    GUEST_RO="$SHARE_ROOT/$RO_NAME"
    GUEST_RW="$SHARE_ROOT/$RW_NAME"
    if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "[ -d \"$GUEST_RO\" ] && [ -d \"$GUEST_RW\" ]" >/dev/null 2>&1; then
        break
    fi

    sleep 1
done

if [[ -z "$MOUNT_LINE" ]]; then
    say "[runner] guest mount output:" >&2
    python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/sbin/mount" >&2 || true
    say "[runner] /Volumes listing:" >&2
    python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/bin/ls -la /Volumes" || true
    action_required "Could not find the shared-folder mountpoint (AppleVirtIOFS) in the guest.\nFix: ensure shared folders are enabled and the guest auto-mounts the VirtioFS share."
fi

if [[ -z "$SHARE_ROOT" || "$SHARE_ROOT" == "$MOUNT_LINE" ]]; then
    action_required "Failed to parse AppleVirtIOFS mountpoint from guest mount output:\n$MOUNT_LINE"
fi

say "[runner] share_root=$SHARE_ROOT"
say "[runner] guest_ro=$GUEST_RO"
say "[runner] guest_rw=$GUEST_RW"

if ! python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "[ -d \"$GUEST_RO\" ] && [ -d \"$GUEST_RW\" ]" >/dev/null 2>&1; then
    say "[runner] share_root listing:" >&2
    python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/bin/ls -la \"$SHARE_ROOT\"" || true
    say "[runner] /Volumes listing:" >&2
    python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/bin/ls -la /Volumes" || true
    action_required "Expected shared folders not found under:\n  $GUEST_RO\n  $GUEST_RW\nFix: ensure --ro/--rw leaf names are unique and the VM is restarted after config changes."
fi

# Validate RO is actually read-only. Some macOS guest/Host API combinations can
# transiently report a mismatched process status immediately after VirtioFS
# mount, so parse the touch exit code explicitly and retry before failing.
ro_check_ok=0
ro_check_output=""
for attempt in 1 2 3; do
    set +e
    ro_check_output="$(
        python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "
          set +e
          ro_check_file=\"$GUEST_RO/.ghostvm_ro_test\"
          /usr/bin/touch \"\$ro_check_file\" >/dev/null 2>&1
          touch_exit=\$?
          /bin/rm -f \"\$ro_check_file\" >/dev/null 2>&1
          /bin/echo \"touch_exit=\$touch_exit\"
          /usr/bin/test \"\$touch_exit\" -ne 0
        " 2>&1
    )"
    ro_check_status=$?
    set -e

    if [[ "$ro_check_status" -eq 0 ]]; then
        ro_check_ok=1
        break
    fi

    if [[ "$ro_check_output" =~ touch_exit=([0-9]+) ]] && [[ "${BASH_REMATCH[1]}" -ne 0 ]]; then
        ro_check_ok=1
        break
    fi

    sleep "$attempt"
done

if [[ "$ro_check_ok" -ne 1 ]]; then
    say "[runner] RO validation output: ${ro_check_output:-<empty>}" >&2
    say "[runner] guest RO listing:" >&2
    python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/ls -lde "$GUEST_RO" >&2 || true
    action_required "RO share appears writable from the guest. Refusing to continue.\nFix: ensure sharedFolders entry for --ro has readOnly=true in config.json (scripts configure this automatically). For disposable staged inputs, also consider removing host write bits before running the VM loop."
fi

# Validate RW is writable. The VirtioFS mount can briefly appear before write
# permissions are usable, especially immediately after snapshot revert.
rw_check_ok=0
rw_check_output=""
for attempt in 1 2 3; do
    set +e
    rw_check_output="$(
        python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/usr/bin/touch \"$GUEST_RW/.ghostvm_rw_test\" && /bin/rm -f \"$GUEST_RW/.ghostvm_rw_test\"" 2>&1
    )"
    rw_check_status=$?
    set -e

    if [[ "$rw_check_status" -eq 0 ]]; then
        rw_check_ok=1
        break
    fi

    sleep "$attempt"
done

if [[ "$rw_check_ok" -ne 1 ]]; then
    say "[runner] RW validation output: ${rw_check_output:-<empty>}" >&2
    action_required "RW share is not writable from the guest. Ensure --rw points to a writable host directory."
fi

# Write the guest run script into the RW share (so quoting is easy and the guest can read it).
GUEST_RUN_SCRIPT_HOST="$HOST_RUN_DIR/guest_run.sh"
cat >"$GUEST_RUN_SCRIPT_HOST" <<GUESTSH
#!/bin/zsh
set -euo pipefail

RUN_DIR="\$(cd -- "\$(dirname -- "\$0")" && pwd)"
MOUNT_LINE="\$(/sbin/mount | /usr/bin/grep -m 1 AppleVirtIOFS || true)"
if [[ -z "\$MOUNT_LINE" ]]; then
  print "[guest] error: AppleVirtIOFS mount not found" >&2
  /sbin/mount >&2 || true
  exit 1
fi
SHARE_ROOT="\${MOUNT_LINE#* on }"
SHARE_ROOT="\${SHARE_ROOT%% \\(*}"
RO_VOL="\$SHARE_ROOT/$RO_NAME"
RW_VOL="\$SHARE_ROOT/$RW_NAME"
WORK_BASE="/Users/Shared/ghostvm-safe-testing/$RUN_ID"
INPUT_DIR="\$WORK_BASE/input"
CMD_FILE="\$RUN_DIR/cmd.txt"
CMD="\$(/bin/cat "\$CMD_FILE")"

mkdir -p "\$WORK_BASE"
mkdir -p "\$RUN_DIR"

exec >"\$RUN_DIR/stdout.log" 2>"\$RUN_DIR/stderr.log"

print "[guest] ro=\$RO_VOL"
print "[guest] rw=\$RW_VOL"
print "[guest] work=\$WORK_BASE"
print "[guest] cmd=\$CMD"

# Copy RO input into guest-local workspace.
/bin/rm -rf "\$INPUT_DIR"
/usr/bin/ditto "\$RO_VOL" "\$INPUT_DIR"

cd "\$INPUT_DIR"

# Capture basic context.
/usr/bin/uname -a >"\$RUN_DIR/uname.txt" 2>&1 || true
/usr/bin/sw_vers >"\$RUN_DIR/sw_vers.txt" 2>&1 || true

# Run user command.
set +e
/bin/zsh -lc "\$CMD"
EXIT_CODE=\$?
set -e
print "\$EXIT_CODE" >"\$RUN_DIR/exit_code"

# If git repo, export patch.
if [[ -d .git ]]; then
  /usr/bin/git status --porcelain=v1 >"\$RUN_DIR/git.status" 2>&1 || true
  /usr/bin/git diff --no-color >"\$RUN_DIR/git.diff" 2>&1 || true
  /usr/bin/git rev-parse HEAD >"\$RUN_DIR/git.head" 2>&1 || true
fi

exit \$EXIT_CODE
GUESTSH
chmod +x "$GUEST_RUN_SCRIPT_HOST"

say "[runner] running guest script"
GUEST_RUN_SCRIPT_GUEST="$GUEST_RW/ghostvm-runs/$VM_NAME/$RUN_ID/guest_run.sh"

# Ensure it is executable in the guest even if host-side permissions didn't propagate.
python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/chmod +x "$GUEST_RUN_SCRIPT_GUEST" >/dev/null 2>&1 || true

# Run the long guest step via Host API exec so we can set a timeout.
EXEC_SH="$(dirname "$0")/ghostvm_exec.sh"
if "$EXEC_SH" --socket "$sock_path" --timeout "$CMD_TIMEOUT" --argv "$GUEST_RUN_SCRIPT_GUEST"; then
    :
else
    rc=$?
    say "[runner] guest command failed (exit=$rc; see logs in $HOST_RUN_DIR)"
    exit "$rc"
fi

say "[runner] guest command OK"

if [[ $KEEP_RUNNING -eq 1 ]]; then
    STARTED_BY_RUNNER=0
    trap - EXIT
    say "[runner] --keep-running set; leaving VM running"
    exit 0
fi

say "[runner] stopping VM"
vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
wait "$START_PID" >/dev/null 2>&1 || true

STARTED_BY_RUNNER=0
trap - EXIT

say "[runner] done"
