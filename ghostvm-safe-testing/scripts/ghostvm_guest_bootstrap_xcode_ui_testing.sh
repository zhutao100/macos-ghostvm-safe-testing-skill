#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_guest_bootstrap_xcode_ui_testing.sh [--xcode-app /Applications/Xcode.app] [--user <guest-user> ...]

Runs inside a disposable macOS guest as root. It prepares Xcode/XCTest UI testing so
headless runs do not stop on first-use developer-tool or Automation Mode prompts.

Actions:
  - selects the requested Xcode.app developer directory
  - accepts the Xcode license and runs first-launch setup
  - enables DevToolsSecurity
  - adds selected users to the _developer group
  - runs automationmodetool enable-automationmode-without-authentication

This is intended for disposable VM images, not real developer hosts.
USAGE
}

say() { printf '%s\n' "$*" >&2; }

XCODE_APP="/Applications/Xcode.app"
USERS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --xcode-app)
            XCODE_APP="$2"
            shift 2
            ;;
        --user)
            USERS+=("$2")
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            say "Unknown argument: $1"
            usage >&2
            exit 2
            ;;
    esac
done

if [[ $(/usr/bin/id -u) -ne 0 ]]; then
    say "ERROR: this script must run as root. Use sudo inside the disposable guest."
    exit 1
fi

# Launchd/GhostTools contexts can have a minimal PATH.
export PATH="/usr/bin:/usr/sbin:/bin:/sbin:/Applications/Xcode.app/Contents/Developer/usr/bin:$PATH"

failures=0
record_failure() {
    failures=$((failures + 1))
    say "[xcode-ui-bootstrap] FAIL: $*"
}

run_or_fail() {
    local label="$1"
    shift
    say "[xcode-ui-bootstrap] $label"
    if "$@"; then
        say "[xcode-ui-bootstrap] OK: $label"
    else
        record_failure "$label"
    fi
}

wait_for_automation_mode_without_auth() {
    local marker="/private/var/db/com.apple.dt.automationmode/no-auth-required"
    local deadline=$((SECONDS + 30))
    while [[ $SECONDS -lt $deadline ]]; do
        if [[ -f "$marker" ]]; then
            /bin/sync
            return 0
        fi
        sleep 1
    done
    say "[xcode-ui-bootstrap] automationmodetool status after timeout:"
    automationmodetool >&2 || true
    return 1
}

if [[ ${#USERS[@]} -eq 0 ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
        USERS+=("$SUDO_USER")
    else
        console_user="$(/usr/bin/stat -f %Su /dev/console 2>/dev/null || true)"
        if [[ -n "$console_user" && "$console_user" != "root" && "$console_user" != "loginwindow" ]]; then
            USERS+=("$console_user")
        fi
    fi
fi

XCODE_DEV_DIR="$XCODE_APP/Contents/Developer"
if [[ -d "$XCODE_DEV_DIR" ]]; then
    run_or_fail "select Xcode developer directory ($XCODE_DEV_DIR)" /usr/bin/xcode-select -s "$XCODE_DEV_DIR"
else
    record_failure "Xcode developer directory not found: $XCODE_DEV_DIR"
fi

if /usr/bin/xcodebuild -version >/dev/null 2>&1; then
    run_or_fail "accept Xcode license" /usr/bin/xcodebuild -license accept
    run_or_fail "run Xcode first-launch setup" /usr/bin/xcodebuild -runFirstLaunch
else
    record_failure "xcodebuild is not usable after xcode-select"
fi

if [[ -x /usr/sbin/DevToolsSecurity ]]; then
    run_or_fail "enable DevToolsSecurity" /usr/sbin/DevToolsSecurity -enable
    /usr/sbin/DevToolsSecurity -status >&2 || true
else
    record_failure "/usr/sbin/DevToolsSecurity not found"
fi

if [[ ${#USERS[@]} -gt 0 ]]; then
    for user in "${USERS[@]}"; do
        if /usr/bin/dscl . -read "/Users/$user" >/dev/null 2>&1; then
            run_or_fail "add $user to _developer" /usr/sbin/dseditgroup -o edit -t user -a "$user" _developer
        else
            record_failure "guest user not found for _developer membership: $user"
        fi
    done
else
    say "[xcode-ui-bootstrap] no guest user detected for _developer membership; continuing"
fi

if command -v automationmodetool >/dev/null 2>&1; then
    run_or_fail "allow Automation Mode without per-run authentication" automationmodetool enable-automationmode-without-authentication
    run_or_fail "verify Automation Mode no-auth marker" wait_for_automation_mode_without_auth
    say "[xcode-ui-bootstrap] automationmodetool status:"
    automationmodetool >&2 || true
else
    record_failure "automationmodetool not found on PATH"
fi

if [[ $failures -ne 0 ]]; then
    say "[xcode-ui-bootstrap] completed with $failures failure(s)"
    exit 1
fi

say "[xcode-ui-bootstrap] completed successfully"
