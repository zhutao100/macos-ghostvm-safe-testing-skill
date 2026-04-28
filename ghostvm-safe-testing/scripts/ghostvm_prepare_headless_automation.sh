#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_prepare_headless_automation.sh \
    --vm <Name> | --bundle /path/to/<Name>.GhostVM \
    [--base-snapshot <name>] \
    [--snapshot <name>] \
    [--cidr <CIDR> ...] \
    [--user <guest-user> ...] \
    [--appleevent-target <bundle-id> ...] \
    [--tcc-client </abs/path> ...] \
    [--tcc-bundle-id <bundle-id> ...] \
    [--skip-local-network] \
    [--skip-tcc] \
    [--skip-safari-js-apple-events] \
    [--prime-automation] \
    [--prime-local-network] \
    [--keep-running]

Prepares a disposable GhostVM guest for unattended automation by combining:

  - offline guest-disk seeding while the VM is stopped
    - Local Network CIDR exemptions (default unless --skip-local-network)
    - baseline TCC grants for /usr/bin/osascript, /usr/libexec/sshd-keygen-wrapper,
      and GhostTools (org.ghostvm.com.ghostvm.guest-tools)
      across Accessibility, ScreenCapture, PostEvent, and AppleEvents to
      System Events / Finder / Safari / Mail (default unless --skip-tcc)
    - Safari's Allow JavaScript from Apple Events preference for detected or
      selected guest users (default unless --skip-safari-js-apple-events)

  - optional interactive priming after boot
    - --prime-automation: triggers AppleEvents prompt in the guest UI
    - --prime-local-network: triggers Local Network prompt in the guest UI

Recommended workflow:
  1) start from a known-good base snapshot (for example, clean-state)
  2) apply offline seed while the VM is stopped
  3) optionally prime any extra app-specific prompts in the guest UI
  4) create a new snapshot (for example, automation-ready)
     - if the snapshot already exists, this script replaces it (delete + recreate)

Examples:
  # Pure offline preparation from a clean base snapshot.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --base-snapshot clean-state \
    --snapshot automation-ready

  # Also allow extra AppleEvents targets and an extra TCC client.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --snapshot automation-ready \
    --appleevent-target com.apple.TextEdit \
    --tcc-client /usr/local/bin/cliclick

  # Boot after seeding and prime any remaining prompts manually.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --snapshot automation-ready \
    --prime-automation \
    --prime-local-network

Defaults:
  Local Network CIDRs are provided by ghostvm_guest_privacy_seed.py:
    10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 fc00::/7 fe80::/10

Exit codes:
  0  preparation completed
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
BASE_SNAPSHOT=""
SNAPSHOT_NAME=""
KEEP_RUNNING=0
PRIME_AUTOMATION=0
PRIME_LOCAL_NETWORK=0
SKIP_LOCAL_NETWORK=0
SKIP_TCC=0
SKIP_SAFARI_JS_APPLE_EVENTS=0

CIDRS=()
USERS=()
APPLEEVENT_TARGETS=()
TCC_CLIENTS=()
TCC_BUNDLE_IDS=()

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
        --base-snapshot)
            BASE_SNAPSHOT="$2"
            shift 2
            ;;
        --snapshot)
            SNAPSHOT_NAME="$2"
            shift 2
            ;;
        --cidr)
            CIDRS+=("$2")
            shift 2
            ;;
        --user)
            USERS+=("$2")
            shift 2
            ;;
        --appleevent-target)
            APPLEEVENT_TARGETS+=("$2")
            shift 2
            ;;
        --tcc-client)
            TCC_CLIENTS+=("$2")
            shift 2
            ;;
        --tcc-bundle-id)
            TCC_BUNDLE_IDS+=("$2")
            shift 2
            ;;
        --skip-local-network)
            SKIP_LOCAL_NETWORK=1
            shift
            ;;
        --skip-tcc)
            SKIP_TCC=1
            shift
            ;;
        --skip-safari-js-apple-events)
            SKIP_SAFARI_JS_APPLE_EVENTS=1
            shift
            ;;
        --prime-automation)
            PRIME_AUTOMATION=1
            shift
            ;;
        --prime-local-network)
            PRIME_LOCAL_NETWORK=1
            shift
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

if [[ -z "$BUNDLE_PATH" ]]; then
    [[ -n "$VM_NAME" ]] || action_required "Missing --vm <Name> (or pass --bundle)"
    ROOT_DIR="$({
        python3 - "$ROOT_DIR" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
    })"
    BUNDLE_PATH="$ROOT_DIR/$VM_NAME.GhostVM"
fi

BUNDLE_PATH="$({
    python3 - "$BUNDLE_PATH" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
})"
VM_NAME="${VM_NAME:-$(basename "$BUNDLE_PATH" .GhostVM)}"

if [[ ! -d "$BUNDLE_PATH" ]]; then
    action_required "VM bundle not found: $BUNDLE_PATH"
fi

if [[ -n "$BASE_SNAPSHOT" && -n "$SNAPSHOT_NAME" && "$BASE_SNAPSHOT" == "$SNAPSHOT_NAME" ]]; then
    action_required "--base-snapshot and --snapshot must differ. Revert from the base, then create a new prepared snapshot."
fi

if [[ $KEEP_RUNNING -eq 1 && -n "$SNAPSHOT_NAME" ]]; then
    action_required "--keep-running cannot be combined with --snapshot because snapshot creation requires the VM to be stopped."
fi

if ! command -v python3 >/dev/null 2>&1; then
    action_required "python3 not found on PATH"
fi

if ! command -v vmctl >/dev/null 2>&1; then
    action_required "vmctl not found on PATH. Install the wrapper:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

VMCTL_PATH="$(command -v vmctl)"
if [[ -L "$VMCTL_PATH" ]]; then
    action_required "vmctl is a symlink ($VMCTL_PATH). Reinstall using the wrapper installer:\n  ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

SEED_PY="$(dirname "$0")/ghostvm_guest_privacy_seed.py"
EXEC_SH="$(dirname "$0")/ghostvm_exec.sh"
REMOTE_EXEC_PY="$(dirname "$0")/ghostvm_remote_exec.py"
for required in "$SEED_PY" "$EXEC_SH" "$REMOTE_EXEC_PY"; do
    [[ -x "$required" || -f "$required" ]] || action_required "Missing required helper: $required"
done

say "[prep] vm=$VM_NAME"
say "[prep] bundle=$BUNDLE_PATH"
[[ -n "$BASE_SNAPSHOT" ]] && say "[prep] base_snapshot=$BASE_SNAPSHOT"
[[ -n "$SNAPSHOT_NAME" ]] && say "[prep] snapshot=$SNAPSHOT_NAME"
[[ $SKIP_LOCAL_NETWORK -eq 1 ]] && say "[prep] local_network=skip"
[[ $SKIP_TCC -eq 1 ]] && say "[prep] tcc=skip"
[[ $SKIP_SAFARI_JS_APPLE_EVENTS -eq 1 ]] && say "[prep] safari_js_apple_events=skip"

PID_FILE="$BUNDLE_PATH/vmctl.pid"
vm_pid_alive() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(tr -d '[:space:]' <"$PID_FILE" | sed 's/^embedded://')"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    /bin/kill -0 "$pid" 2>/dev/null
}

wait_for_vm_stop() {
    local deadline=$((SECONDS + 240))
    while [[ $SECONDS -lt $deadline ]]; do
        if ! vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 && ! vm_pid_alive; then
            return 0
        fi
        sleep 1
    done
    return 1
}

disk_image_root_device() {
    python3 - "$BUNDLE_PATH/disk.img" <<'PY'
import os
import plistlib
import subprocess
import sys
from pathlib import Path

disk = Path(sys.argv[1]).expanduser().resolve()
try:
    payload = plistlib.loads(subprocess.check_output(["hdiutil", "info", "-plist"]))
except Exception:
    raise SystemExit(1)

for image in payload.get("images", []):
    img_path = image.get("image-path")
    if not img_path:
        continue
    try:
        if Path(str(img_path)).expanduser().resolve() != disk:
            continue
    except OSError:
        continue
    devs = [str(e.get("dev-entry")) for e in image.get("system-entities", []) if e.get("dev-entry")]
    if devs:
        print(sorted(devs, key=lambda s: (len(s), s))[0])
        raise SystemExit(0)
raise SystemExit(1)
PY
}

detach_disk_image_if_attached() {
    [[ -f "$BUNDLE_PATH/disk.img" ]] || return 0
    local root_device
    root_device="$(disk_image_root_device 2>/dev/null || true)"
    [[ -n "$root_device" ]] || return 0
    say "[prep] detaching existing disk image mount: $root_device"
    hdiutil detach "$root_device" >/dev/null 2>&1 || hdiutil detach -force "$root_device" >/dev/null 2>&1 ||
        action_required "Failed to detach already-mounted disk image ($root_device). Close any Finder windows or processes using it, then retry."
}

snapshot_exists() {
    local name="$1"
    local listing
    if ! listing="$(vmctl snapshot "$BUNDLE_PATH" list 2>/dev/null)"; then
        return 2
    fi
    local line
    while IFS= read -r line; do
        [[ "$line" == "$name" ]] && return 0
    done <<<"$listing"
    return 1
}

create_snapshot() {
    local name="$1"
    if snapshot_exists "$name"; then
        say "[prep] deleting existing snapshot: $name"
        if ! vmctl snapshot "$BUNDLE_PATH" delete "$name" >/dev/null 2>&1; then
            action_required "Failed to delete existing snapshot '$name'. Ensure the VM is stopped and the snapshot name is valid."
        fi
    else
        local rc=$?
        if [[ $rc -eq 2 ]]; then
            action_required "Failed to list snapshots. Ensure the VM bundle exists and vmctl can access it."
        fi
    fi

    say "[prep] creating snapshot: $name"
    if ! vmctl snapshot "$BUNDLE_PATH" create "$name" >/dev/null 2>&1; then
        action_required "Failed to create snapshot '$name'. Ensure the VM is stopped and the snapshot name is valid."
    fi
    say "[prep] snapshot created: $name"
}

stop_vm_if_running() {
    if vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 || vm_pid_alive; then
        say "[prep] stopping running VM"
        vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
        wait_for_vm_stop || action_required "Timed out waiting for VM to stop. Stop it in GhostVM GUI and retry."
    fi
}

if vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 || vm_pid_alive; then
    stop_vm_if_running
fi

detach_disk_image_if_attached

if [[ -n "$BASE_SNAPSHOT" ]]; then
    say "[prep] reverting base snapshot"
    if ! vmctl snapshot "$BUNDLE_PATH" revert "$BASE_SNAPSHOT" >/dev/null 2>&1; then
        action_required "Failed to revert snapshot '$BASE_SNAPSHOT'. Ensure it exists and the VM is stopped."
    fi
fi

seed_args=(python3 "$SEED_PY" --bundle "$BUNDLE_PATH")
if [[ $SKIP_LOCAL_NETWORK -eq 1 ]]; then
    seed_args+=(--skip-local-network)
fi
if [[ $SKIP_TCC -eq 1 ]]; then
    seed_args+=(--skip-tcc)
fi
if [[ $SKIP_SAFARI_JS_APPLE_EVENTS -eq 1 ]]; then
    seed_args+=(--skip-safari-js-apple-events)
fi
for cidr in "${CIDRS[@]}"; do
    seed_args+=(--cidr "$cidr")
done
for user in "${USERS[@]}"; do
    seed_args+=(--user "$user")
done
for target in "${APPLEEVENT_TARGETS[@]}"; do
    seed_args+=(--appleevent-target "$target")
done
for client in "${TCC_CLIENTS[@]}"; do
    seed_args+=(--tcc-client "$client")
done
for bundle_id in "${TCC_BUNDLE_IDS[@]}"; do
    seed_args+=(--tcc-bundle-id "$bundle_id")
done

say "[prep] applying offline guest-disk seed"
"${seed_args[@]}"

if [[ $PRIME_AUTOMATION -eq 0 && $PRIME_LOCAL_NETWORK -eq 0 ]]; then
    if [[ $KEEP_RUNNING -eq 1 ]]; then
        say "[prep] note: --keep-running has no effect because no priming step booted the guest"
    fi
    if [[ -z "$SNAPSHOT_NAME" ]]; then
        say "[prep] done (offline seed applied; no snapshot requested)"
        exit 0
    fi

    create_snapshot "$SNAPSHOT_NAME"
    exit 0
fi

STARTED_BY_SCRIPT=0
START_LOG=""
START_PID=""
sock_path=""

cleanup() {
    if [[ $STARTED_BY_SCRIPT -eq 1 && $KEEP_RUNNING -ne 1 ]]; then
        say "[prep] cleanup: stopping VM"
        vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
        if [[ -n "$START_PID" ]]; then
            wait "$START_PID" >/dev/null 2>&1 || true
        fi
    fi
    if [[ -n "$START_LOG" ]]; then
        rm -f "$START_LOG" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

STARTED_BY_SCRIPT=1
START_LOG="$(mktemp -t ghostvm-prep-start.XXXXXX)"
say "[prep] starting VM via GhostVMHelper (background)"
vmctl start "$BUNDLE_PATH" >"$START_LOG" 2>&1 &
START_PID=$!

deadline=$((SECONDS + 240))
while [[ $SECONDS -lt $deadline ]]; do
    if sock_path="$(vmctl socket "$BUNDLE_PATH" 2>/dev/null)"; then
        break
    fi
    if ! /bin/kill -0 "$START_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

if [[ -z "$sock_path" ]]; then
    say "[prep] start logs (first 200 lines):"
    sed -n '1,200p' "$START_LOG" >&2 || true
    action_required "Timed out waiting for Host API socket. See $START_LOG and references/troubleshooting.md"
fi

say "[prep] socket=$sock_path"
if ! vmctl remote --socket "$sock_path" health >/dev/null 2>&1; then
    action_required "GhostTools /health failed. Ensure GhostTools is installed + running in the guest. See references/troubleshooting.md"
fi

deadline=$((SECONDS + 30))
while [[ $SECONDS -lt $deadline ]]; do
    if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/echo ok >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/echo ok >/dev/null 2>&1; then
    action_required "Guest exec is not responding yet (even though /health is OK). Wait for login + GhostTools initialization, then retry. See references/remote-exec.md"
fi

prime_automation_prompts() {
    say "[prep] priming Automation (AppleEvents) prompt via osascript → System Events"
    say "[prep] Watch the guest UI and click Allow/OK if prompted."
    "$EXEC_SH" --socket "$sock_path" --timeout 300 --argv \
        /usr/bin/osascript \
        -e 'tell application "System Events" to get name of every process' \
        >/dev/null || true
}

prime_local_network_prompt() {
    say "[prep] priming Local Network prompt via dns-sd browse (expected to time out)"
    say "[prep] Watch the guest UI and click Allow/OK if prompted."
    say "[prep] Use this for traffic that is not covered by the seeded CIDR exemptions."
    set +e
    "$EXEC_SH" --socket "$sock_path" --timeout 60 --argv /usr/bin/dns-sd -B _services._dns-sd._udp local. >/dev/null 2>&1
    set -e
}

if [[ $PRIME_AUTOMATION -eq 1 ]]; then
    prime_automation_prompts
fi
if [[ $PRIME_LOCAL_NETWORK -eq 1 ]]; then
    prime_local_network_prompt
fi

if [[ -z "$SNAPSHOT_NAME" ]]; then
    say "[prep] done (no snapshot requested)"
    exit 0
fi

say "[prep] stopping VM (snapshot requires stopped VM)"
vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
wait_for_vm_stop || action_required "Timed out waiting for VM to stop. Stop it in GhostVM GUI and retry snapshot creation."

create_snapshot "$SNAPSHOT_NAME"

STARTED_BY_SCRIPT=0
trap - EXIT
cleanup
