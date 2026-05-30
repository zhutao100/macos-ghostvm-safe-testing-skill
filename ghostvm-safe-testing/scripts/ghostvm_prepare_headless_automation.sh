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
    [--tcc-service <service> ...] \
    [--xcode-ui-testing] \
    [--xcode-app /Applications/Xcode.app] \
    [--sudo-password-env GHOSTVM_GUEST_SUDO_PASSWORD] \
    [--replace-snapshot] \
    [--skip-local-network] \
    [--skip-tcc] \
    [--skip-safari-js-apple-events] \
    [--skip-automation-tuning] \
    [--keep-security-responses] \
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
    - vanilla macOS automation tuning (default unless --skip-automation-tuning):
      disable automatic update downloads/installs, suppress timed lock, suppress
      Time Machine new-disk prompts, and opt the disposable data volume out of Spotlight indexing
    - with --xcode-ui-testing: common XCTest/Xcode UI-testing TCC clients,
      including Xcode.app, Xcode Helper.app, xcodebuild, and xcrun candidates

  - noninteractive in-guest bootstrap after boot when --xcode-ui-testing is used
    - accepts Xcode license and runs first-launch setup where possible
    - enables DevToolsSecurity and adds selected users to _developer
    - runs automationmodetool to suppress the XCTest Automation Mode auth prompt

  - optional interactive priming after boot
    - --prime-automation: triggers AppleEvents prompt in the guest UI
    - --prime-local-network: triggers Local Network prompt in the guest UI

Recommended workflow:
  1) start from a known-good base snapshot (for example, clean-state)
  2) apply offline seed while the VM is stopped
  3) optionally prime any extra app-specific prompts in the guest UI
  4) create a new snapshot (for example, automation-ready)
     - if the snapshot already exists, this script fails without deleting it
     - pass --replace-snapshot only after the user explicitly asks to overwrite

Examples:
  # Pure offline preparation from a clean base snapshot.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --base-snapshot clean-state \
    --snapshot automation-ready

  # Explicitly overwrite an existing prepared snapshot.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --base-snapshot clean-state \
    --snapshot automation-ready \
    --replace-snapshot

  # Also allow extra AppleEvents targets and an extra TCC client.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --snapshot automation-ready \
    --appleevent-target com.apple.TextEdit \
    --tcc-client /usr/local/bin/cliclick

  # Prepare for Xcode/XCTest macOS UI automation in a disposable VM.
  ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --base-snapshot clean-state \
    --snapshot xcode-ui-ready \
    --xcode-ui-testing \
    --user agent

  # Same, but use a known disposable-guest sudo password from the host environment.
  GHOSTVM_GUEST_SUDO_PASSWORD=admin ghostvm_prepare_headless_automation.sh \
    --vm Dev \
    --base-snapshot clean-state \
    --snapshot xcode-ui-ready \
    --xcode-ui-testing \
    --user admin

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
REPLACE_SNAPSHOT=0
KEEP_RUNNING=0
PRIME_AUTOMATION=0
PRIME_LOCAL_NETWORK=0
SKIP_LOCAL_NETWORK=0
SKIP_TCC=0
SKIP_SAFARI_JS_APPLE_EVENTS=0
SKIP_AUTOMATION_TUNING=0
KEEP_SECURITY_RESPONSES=0
XCODE_UI_TESTING=0
XCODE_APP="/Applications/Xcode.app"
SUDO_PASSWORD_ENV="GHOSTVM_GUEST_SUDO_PASSWORD"

CIDRS=()
USERS=()
APPLEEVENT_TARGETS=()
TCC_CLIENTS=()
TCC_BUNDLE_IDS=()
TCC_SERVICES=()

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
        --replace-snapshot)
            REPLACE_SNAPSHOT=1
            shift
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
        --tcc-service)
            TCC_SERVICES+=("$2")
            shift 2
            ;;
        --xcode-ui-testing)
            XCODE_UI_TESTING=1
            shift
            ;;
        --xcode-app)
            XCODE_UI_TESTING=1
            XCODE_APP="$2"
            shift 2
            ;;
        --sudo-password-env)
            SUDO_PASSWORD_ENV="$2"
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
        --skip-automation-tuning)
            SKIP_AUTOMATION_TUNING=1
            shift
            ;;
        --keep-security-responses)
            KEEP_SECURITY_RESPONSES=1
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

if ! [[ "$SUDO_PASSWORD_ENV" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    action_required "--sudo-password-env must be a shell variable name, got: $SUDO_PASSWORD_ENV"
fi

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

if [[ $REPLACE_SNAPSHOT -eq 1 && -z "$SNAPSHOT_NAME" ]]; then
    action_required "--replace-snapshot requires --snapshot <name>."
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
TUNE_PY="$(dirname "$0")/ghostvm_guest_tune_automation.py"
GUARD_PY="$(dirname "$0")/ghostvm_automation_guard.py"
EXEC_SH="$(dirname "$0")/ghostvm_exec.sh"
REMOTE_EXEC_PY="$(dirname "$0")/ghostvm_remote_exec.py"
BOOTSTRAP_XCODE_UI_SH="$(dirname "$0")/ghostvm_guest_bootstrap_xcode_ui_testing.sh"
for required in "$SEED_PY" "$TUNE_PY" "$GUARD_PY" "$EXEC_SH" "$REMOTE_EXEC_PY" "$BOOTSTRAP_XCODE_UI_SH"; do
    [[ -x "$required" || -f "$required" ]] || action_required "Missing required helper: $required"
done

say "[prep] vm=$VM_NAME"
say "[prep] bundle=$BUNDLE_PATH"
[[ -n "$BASE_SNAPSHOT" ]] && say "[prep] base_snapshot=$BASE_SNAPSHOT"
[[ -n "$SNAPSHOT_NAME" ]] && say "[prep] snapshot=$SNAPSHOT_NAME"
[[ $REPLACE_SNAPSHOT -eq 1 ]] && say "[prep] replace_snapshot=enabled"
[[ $SKIP_LOCAL_NETWORK -eq 1 ]] && say "[prep] local_network=skip"
[[ $SKIP_TCC -eq 1 ]] && say "[prep] tcc=skip"
[[ $SKIP_SAFARI_JS_APPLE_EVENTS -eq 1 ]] && say "[prep] safari_js_apple_events=skip"
if [[ $SKIP_AUTOMATION_TUNING -eq 0 ]]; then
    say "[prep] automation_tuning=seed"
else
    say "[prep] automation_tuning=skip"
fi
if [[ $XCODE_UI_TESTING -eq 1 ]]; then
    say "[prep] xcode_ui_testing=enable"
    say "[prep] xcode_app=$XCODE_APP"
    if [[ -n "${!SUDO_PASSWORD_ENV:-}" ]]; then
        say "[prep] guest_sudo_password_env=$SUDO_PASSWORD_ENV"
    fi
fi

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
        if [[ $REPLACE_SNAPSHOT -eq 1 ]]; then
            say "[prep] deleting existing snapshot: $name"
            if ! vmctl snapshot "$BUNDLE_PATH" delete "$name" >/dev/null 2>&1; then
                action_required "Failed to delete existing snapshot '$name'. Ensure the VM is stopped and the snapshot name is valid."
            fi
        else
            action_required "Snapshot '$name' already exists. Choose a new --snapshot name, or rerun with --replace-snapshot only after the user explicitly asks to overwrite it."
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

preflight_target_snapshot() {
    local name="$1"
    if snapshot_exists "$name"; then
        if [[ $REPLACE_SNAPSHOT -eq 1 ]]; then
            say "[prep] existing snapshot will be replaced: $name"
        else
            action_required "Snapshot '$name' already exists. Choose a new --snapshot name, or rerun with --replace-snapshot only after the user explicitly asks to overwrite it."
        fi
    else
        local rc=$?
        if [[ $rc -eq 2 ]]; then
            action_required "Failed to list snapshots. Ensure the VM bundle exists and vmctl can access it."
        fi
    fi
}

stop_vm_if_running() {
    if vmctl socket "$BUNDLE_PATH" >/dev/null 2>&1 || vm_pid_alive; then
        say "[prep] stopping running VM"
        vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
        wait_for_vm_stop || action_required "Timed out waiting for VM to stop. Stop it in GhostVM GUI and retry."
    fi
}

if [[ -n "$SNAPSHOT_NAME" ]]; then
    preflight_target_snapshot "$SNAPSHOT_NAME"
fi

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
for service in "${TCC_SERVICES[@]}"; do
    seed_args+=(--tcc-service "$service")
done
if [[ $XCODE_UI_TESTING -eq 1 ]]; then
    seed_args+=(--xcode-ui-testing --xcode-app "$XCODE_APP")
fi

say "[prep] applying offline guest-disk seed"
"${seed_args[@]}"

if [[ $SKIP_AUTOMATION_TUNING -eq 0 ]]; then
    tune_args=(python3 "$TUNE_PY" --bundle "$BUNDLE_PATH")
    if [[ $KEEP_SECURITY_RESPONSES -eq 1 ]]; then
        tune_args+=(--keep-security-responses)
    fi
    for user in "${USERS[@]}"; do
        tune_args+=(--user "$user")
    done
    say "[prep] applying offline vanilla-macOS automation tuning"
    "${tune_args[@]}"
fi

STARTED_BY_SCRIPT=0
START_LOG=""
START_PID=""
sock_path=""
AUTOMATION_STATE=""
AUTOMATION_STATE_RESTORED=0
GUARD_STATE_DIR=""

ensure_guard_state_path() {
    if [[ -z "$AUTOMATION_STATE" ]]; then
        GUARD_STATE_DIR="$(mktemp -d -t ghostvm-prep-automation.XXXXXX)"
        AUTOMATION_STATE="$GUARD_STATE_DIR/automation_state.json"
    fi
}

apply_automation_guard() {
    ensure_guard_state_path
    say "[prep] applying temporary automation guards"
    if ! python3 "$GUARD_PY" apply \
        --bundle "$BUNDLE_PATH" \
        --state "$AUTOMATION_STATE" \
        --force-nat \
        --disable-port-forwards \
        --clear-shared-folders \
        --configure-helper-defaults >/dev/null; then
        action_required "Failed to apply GhostVM automation guards. See config.json and $AUTOMATION_STATE."
    fi
}

restore_automation_guard() {
    if [[ -n "${AUTOMATION_STATE:-}" && -f "$AUTOMATION_STATE" && $AUTOMATION_STATE_RESTORED -ne 1 ]]; then
        say "[prep] restoring GhostVM config/defaults"
        if python3 "$GUARD_PY" restore --state "$AUTOMATION_STATE" --delete-state >/dev/null 2>&1; then
            AUTOMATION_STATE_RESTORED=1
            if [[ -n "$GUARD_STATE_DIR" ]]; then
                rmdir "$GUARD_STATE_DIR" >/dev/null 2>&1 || true
            fi
            return 0
        fi
        AUTOMATION_STATE_RESTORED=0
        say "[prep] error: failed to restore automation state from $AUTOMATION_STATE"
        return 1
    fi
    return 0
}

print_guard_restore_command() {
    say "[prep] automation state remains active: $AUTOMATION_STATE"
    say "[prep] finish/restore command:"
    say "  python3 $GUARD_PY restore --state $AUTOMATION_STATE --stop-vm --delete-state"
}

cleanup() {
    local status=$?
    if [[ $KEEP_RUNNING -eq 1 && $STARTED_BY_SCRIPT -eq 1 ]]; then
        if [[ -n "${AUTOMATION_STATE:-}" && -f "$AUTOMATION_STATE" && $AUTOMATION_STATE_RESTORED -ne 1 ]]; then
            say "[prep] --keep-running set; leaving VM/config state for inspection"
            print_guard_restore_command
        fi
    else
        if [[ $STARTED_BY_SCRIPT -eq 1 ]]; then
            say "[prep] cleanup: stopping VM"
            vmctl stop "$BUNDLE_PATH" >/dev/null 2>&1 || true
            if [[ -n "$START_PID" ]]; then
                wait "$START_PID" >/dev/null 2>&1 || true
            fi
        fi
        if ! restore_automation_guard; then
            [[ $status -eq 0 ]] && status=1
        fi
    fi
    if [[ -n "$START_LOG" ]]; then
        rm -f "$START_LOG" >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap cleanup EXIT

if [[ $PRIME_AUTOMATION -eq 0 && $PRIME_LOCAL_NETWORK -eq 0 && $XCODE_UI_TESTING -eq 0 ]]; then
    if [[ $KEEP_RUNNING -eq 1 ]]; then
        say "[prep] note: --keep-running has no effect because no priming step booted the guest"
    fi
    if [[ -z "$SNAPSHOT_NAME" ]]; then
        say "[prep] done (offline seed applied; no snapshot requested)"
        exit 0
    fi

    apply_automation_guard
    create_snapshot "$SNAPSHOT_NAME"
    trap - EXIT
    cleanup
fi

apply_automation_guard
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
say "[prep] waiting for GhostTools /health"
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

shell_quote() {
    printf '%q' "$1"
}

run_xcode_ui_testing_bootstrap() {
    say "[prep] running Xcode UI-testing guest bootstrap (Automation Mode + Developer Tools)"
    say "[prep] this requires noninteractive sudo inside the disposable guest, or a prior manual bootstrap"

    local bootstrap_b64
    bootstrap_b64="$(/usr/bin/base64 <"$BOOTSTRAP_XCODE_UI_SH" | tr -d '\n')"

    local sudo_password_b64=""
    if [[ -n "${!SUDO_PASSWORD_ENV:-}" ]]; then
        sudo_password_b64="$(printf '%s' "${!SUDO_PASSWORD_ENV}" | /usr/bin/base64 | tr -d '\n')"
    fi

    local xcode_app_q
    xcode_app_q="$(shell_quote "$XCODE_APP")"

    local user_args=""
    local user
    for user in "${USERS[@]}"; do
        user_args+=" --user $(shell_quote "$user")"
    done

    local remote_cmd
    remote_cmd="$(
        cat <<EOF
set -euo pipefail
script_dir=/Users/Shared/ghostvm-safe-testing
script="\$script_dir/ghostvm_guest_bootstrap_xcode_ui_testing.sh"
mkdir -p "\$script_dir"
if ! printf '%s' '$bootstrap_b64' | /usr/bin/base64 -D >"\$script" 2>/dev/null; then
  printf '%s' '$bootstrap_b64' | /usr/bin/base64 --decode >"\$script"
fi
chmod +x "\$script"
if [[ -n '$sudo_password_b64' ]]; then
  if ! sudo_password=\$(printf '%s' '$sudo_password_b64' | /usr/bin/base64 -D 2>/dev/null); then
    sudo_password=\$(printf '%s' '$sudo_password_b64' | /usr/bin/base64 --decode)
  fi
  printf '%s\n' "\$sudo_password" | /usr/bin/sudo -S -p '' "\$script" --xcode-app $xcode_app_q$user_args
else
  /usr/bin/sudo -n "\$script" --xcode-app $xcode_app_q$user_args
fi
EOF
    )"

    if ! "$EXEC_SH" --socket "$sock_path" --timeout 1800 --argv /bin/zsh -lc "$remote_cmd"; then
        action_required "Xcode UI-testing bootstrap failed. This setup requires noninteractive sudo in the disposable guest. Set $SUDO_PASSWORD_ENV to the disposable guest user's sudo password, run the VM once and execute 'sudo /Users/Shared/ghostvm-safe-testing/ghostvm_guest_bootstrap_xcode_ui_testing.sh --xcode-app $XCODE_APP' inside the guest, or configure temporary passwordless sudo before rerunning."
    fi
}

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

if [[ $XCODE_UI_TESTING -eq 1 ]]; then
    run_xcode_ui_testing_bootstrap
fi

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
