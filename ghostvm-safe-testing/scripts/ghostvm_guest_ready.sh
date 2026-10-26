#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_guest_ready.sh --vm <Name> [--bundle /path/to/<Name>.GhostVM] [--root ~/VMs] [--require-rosetta] [--require-ghosttools-prompts-clear] [--require-xcode-ui-testing] [--require-python <major.minor>] [--python <guest-executable>]

Checks (inside the guest, via Host API):
  - Host helper /health followed by successful guest commands
  - Xcode Command Line Tools present (xcode-select -p)
  - (optional) selected Python version and functional native stdlib imports
    when --require-python is set; --python defaults to python3 in the guest login PATH
  - (optional) Rosetta present (pkgutil receipt) when --require-rosetta is set
  - (optional) GhostTools guest setup window prerequisites when
    --require-ghosttools-prompts-clear is set
  - (optional) Xcode UI testing readiness when --require-xcode-ui-testing is set:
    xcodebuild usable, Automation Mode does not require user authentication,
    and DevToolsSecurity is enabled.

Exit codes:
  0  guest is "dev-ready" for common CLI workflows (CLT present; rosetta optional unless required)
  1  ACTION REQUIRED (human)

Notes:
  - This script assumes the VM is already running in non-headless mode (Host API socket exists).
  - For full provisioning guidance, see: references/macos-dev-testing-ready.md
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
REQUIRE_ROSETTA=0
REQUIRE_GHOSTTOOLS_PROMPTS_CLEAR=0
REQUIRE_XCODE_UI_TESTING=0
REQUIRE_PYTHON=""
GUEST_PYTHON="python3"

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
        --require-rosetta)
            REQUIRE_ROSETTA=1
            shift
            ;;
        --require-ghosttools-prompts-clear)
            REQUIRE_GHOSTTOOLS_PROMPTS_CLEAR=1
            shift
            ;;
        --require-xcode-ui-testing)
            REQUIRE_XCODE_UI_TESTING=1
            shift
            ;;
        --require-python)
            REQUIRE_PYTHON="$2"
            shift 2
            ;;
        --python)
            GUEST_PYTHON="$2"
            shift 2
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

if [[ -n "$REQUIRE_PYTHON" && ! "$REQUIRE_PYTHON" =~ ^[0-9]+\.[0-9]+$ ]]; then
    action_required "--require-python must be a major.minor version, for example 3.13"
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
    action_required "vmctl not found on PATH. Install the wrapper:\n  scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

VMCTL_PATH="$(command -v vmctl)"
if [[ -L "$VMCTL_PATH" ]]; then
    action_required "vmctl is a symlink ($VMCTL_PATH). Reinstall using the wrapper installer:\n  scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app"
fi

sock_path=""
if ! sock_path="$(vmctl socket "$BUNDLE_PATH" 2>/dev/null)"; then
    action_required "Host API socket not found. Ensure the VM is running (no --headless) and GhostVMHelper is active.\nTry:\n  scripts/ghostvm_doctor.sh --vm $VM_NAME"
fi

say "[guest-ready] vm=$VM_NAME"
say "[guest-ready] socket=$sock_path"

if ! vmctl remote --socket "$sock_path" health >/dev/null 2>&1; then
    action_required "Host API /health failed. Check GhostVMHelper and its socket."
fi

REMOTE_EXEC_PY="$(dirname "$0")/ghostvm_remote_exec.py"
EXEC_SH="$(dirname "$0")/ghostvm_exec.sh"

# Determine guest arch.
arch="$(python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /usr/bin/arch 2>/dev/null | tr -d '[:space:]' || true)"
if [[ -z "$arch" ]]; then
    arch="unknown"
fi

say "[guest-ready] arch=$arch"

missing=0

check_ok() {
    say "[OK] $*"
}

check_warn() {
    say "[MISSING] $*"
    missing=1
}

if [[ -n "$REQUIRE_PYTHON" ]]; then
    python_check="$(
        cat <<'PY'
import sys
minimum = tuple(map(int, sys.argv[1].split(".")))
print("Python", sys.version.split()[0], "at", sys.executable, flush=True)
if sys.version_info[:2] < minimum:
    raise SystemExit("Python " + sys.argv[1] + "+ is required; select a modern guest interpreter")
import bz2, ctypes, lzma, plistlib, sqlite3, ssl, xml.parsers.expat, zlib
assert plistlib.loads(b"<plist version='1.0'><dict><key>ok</key><true/></dict></plist>") == {"ok": True}
assert sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,)
print("Native stdlib imports and XML/SQLite operations OK")
PY
    )"
    if python_report="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv \
        /bin/zsh -lc 'exec "$@"' _ "$GUEST_PYTHON" -c "$python_check" "$REQUIRE_PYTHON" 2>&1)" &&
        [[ "$python_report" == *"Native stdlib imports and XML/SQLite operations OK"* ]]; then
        check_ok "$python_report"
    else
        check_warn "Guest Python is too old, unavailable, or has broken stdlib dependencies"
        printf '%s\n' "$python_report" >&2
        say "  Fix the interpreter inside a disposable clone, recheck imports, then verify a cold boot before replacing the prepared snapshot."
        say "  Homebrew versioned Python may require its libexec/bin on PATH or an explicit --python /opt/homebrew/bin/python3.X."
    fi
fi

# Xcode CLT presence.
if clt_report="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv /usr/bin/xcode-select -p 2>&1)" &&
    [[ "$clt_report" == /* ]]; then
    check_ok "Xcode Command Line Tools detected (xcode-select -p)"
else
    check_warn "Xcode Command Line Tools could not be verified (missing tools or failed guest request)"
    [[ -z "$clt_report" ]] || printf '%s\n' "$clt_report" >&2
    say "  Fix (interactive): /usr/bin/xcode-select --install"
    say "  Fix (managed): install 'Command Line Tools for Xcode' via System Settings → Software Update (or softwareupdate label install)"
fi

# Rosetta (optional).
if [[ "$arch" == "arm64" ]]; then
    if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/usr/sbin/pkgutil --pkg-info com.apple.pkg.RosettaUpdateAuto >/dev/null 2>&1" >/dev/null 2>&1; then
        check_ok "Rosetta receipt present (com.apple.pkg.RosettaUpdateAuto)"
    else
        if [[ $REQUIRE_ROSETTA -eq 1 ]]; then
            check_warn "Rosetta missing (required)"
        else
            say "[OPTIONAL] Rosetta not detected"
        fi
        say "  Install (user-driven): launch an Intel-only binary; macOS prompts to install Rosetta"
        say "  Install (CLI): sudo /usr/sbin/softwareupdate --install-rosetta --agree-to-license"
    fi
else
    say "[SKIP] Rosetta check (guest arch is not arm64)"
fi

if [[ $REQUIRE_GHOSTTOOLS_PROMPTS_CLEAR -eq 1 ]]; then
    ghosttools_prompt_check="$(
        cat <<'GUESTSH'
set -euo pipefail
domain="org.ghostvm.com.ghostvm.guest-tools"
autostart_key="org.ghostvm.ghosttools.autoStartEnabled"
autoupdate_key="org.ghostvm.ghosttools.autoUpdateEnabled"
exe="/Applications/GhostTools.app/Contents/MacOS/GhostTools"
plist="$HOME/Library/LaunchAgents/$domain.plist"
missing=0

if [[ -x "$exe" ]]; then
  echo "[OK] GhostTools is installed in /Applications"
else
  echo "[MISSING] GhostTools is not installed at $exe"
  missing=1
fi

if [[ "$(/usr/bin/defaults read "$domain" "$autostart_key" 2>/dev/null || true)" == "1" ]]; then
  echo "[OK] GhostTools auto-start default is enabled"
else
  echo "[MISSING] GhostTools auto-start default is not enabled"
  missing=1
fi

if [[ "$(/usr/bin/defaults read "$domain" "$autoupdate_key" 2>/dev/null || true)" == "1" ]]; then
  echo "[OK] GhostTools auto-update default is enabled"
else
  echo "[MISSING] GhostTools auto-update default is not enabled"
  missing=1
fi

if [[ -f "$plist" ]] &&
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$plist" 2>/dev/null || true)" == "$exe" ]] &&
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :RunAtLoad' "$plist" 2>/dev/null || true)" == "true" ]]; then
  echo "[OK] GhostTools LaunchAgent points at /Applications"
else
  echo "[MISSING] GhostTools LaunchAgent is absent or does not point at /Applications"
  missing=1
fi

scheme_handler_ok() {
  local scheme="$1"
  local handler
  handler="$(/usr/bin/osascript -l JavaScript -e '
    function run(argv) {
      ObjC.import("AppKit");
      var url = $.NSURL.URLWithString(argv[0] + "://ghostvm.invalid/");
      var app = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL(url);
      return ObjC.unwrap($.NSBundle.bundleWithURL(app).bundleIdentifier);
    }
  ' "$scheme" 2>/dev/null)" || return 1
  [[ "$handler" == "$domain" ]]
}

for scheme in http https; do
  if scheme_handler_ok "$scheme"; then
    echo "[OK] GhostTools is default handler for $scheme"
  else
    echo "[MISSING] GhostTools is not the default handler for $scheme"
    missing=1
  fi
done

for consent_process in CoreServicesUIAgent UserNotificationCenter "Setup Assistant"; do
  blocking_windows="$(/usr/bin/osascript -e '
    on run argv
      set processName to item 1 of argv
      tell application "System Events"
        if exists process processName then
          if processName is "Setup Assistant" then
            return "onboarding process is still running"
          end if
          return count of windows of process processName
        end if
        return 0
      end tell
    end run
  ' "$consent_process" 2>/dev/null)" || blocking_windows="unknown"
  if [[ "$blocking_windows" == "0" ]]; then
    echo "[OK] No $consent_process automation gate remains"
  else
    echo "[MISSING] $consent_process gate: $blocking_windows; inspect the guest before UI tests"
    missing=1
  fi
done

exit "$missing"
GUESTSH
    )"

    # The CLI omits the guest deadline; send it explicitly for this compound scan.
    if ghosttools_prompt_report="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv /bin/zsh -lc "$ghosttools_prompt_check" 2>&1)"; then
        while IFS= read -r line; do
            [[ -n "$line" ]] && say "$line"
        done <<<"$ghosttools_prompt_report"
        check_ok "GhostTools setup and system-alert prerequisites are satisfied"
    else
        check_warn "GhostTools setup or system-alert prerequisites are incomplete"
        if [[ -n "$ghosttools_prompt_report" ]]; then
            printf '%s\n' "$ghosttools_prompt_report" | sed 's/^/  /' >&2
        fi
        say "  Inspect CoreServicesUIAgent, UserNotificationCenter, and Setup Assistant inside the disposable guest; complete onboarding and resolve blocking browser/account alerts before UI tests."
        say "  Fix: inside the disposable guest, run GhostTools from /Applications once, enable Auto Start and Auto Update, set it as the default browser, answer the notification prompt, then snapshot."
        say "  Note: default-browser readiness prevents GhostTools Setup and URL forwarding prompts; notification authorization may still require one interactive approval on fresh macOS installs."
    fi
fi

if [[ $REQUIRE_XCODE_UI_TESTING -eq 1 ]]; then
    if xcode_report="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv /usr/bin/xcodebuild -version 2>&1)" &&
        [[ "$xcode_report" == *"Xcode "* && "$xcode_report" == *"Build version "* ]]; then
        check_ok "xcodebuild is usable"
    else
        check_warn "xcodebuild could not be verified (missing tools or failed guest request)"
        [[ -z "$xcode_report" ]] || printf '%s\n' "$xcode_report" >&2
        say "  Fix: install/select Xcode, then run scripts/ghostvm_prepare_headless_automation.sh --xcode-ui-testing"
    fi

    if automation_status="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv /usr/bin/automationmodetool 2>&1)" &&
        [[ "$automation_status" == *"DOES NOT REQUIRE"* ]]; then
        check_ok "Automation Mode does not require user authentication"
    else
        check_warn "Automation Mode without authentication could not be verified"
        if [[ -n "$automation_status" ]]; then
            say "  automationmodetool output:"
            printf '%s
' "$automation_status" | sed 's/^/    /' >&2
        fi
        say "  Fix: run scripts/ghostvm_prepare_xcode_ui_testing.sh, optionally with GHOSTVM_GUEST_SUDO_PASSWORD for disposable guests, or inside the guest run:"
        say "    sudo /Users/Shared/ghostvm-safe-testing/ghostvm_guest_bootstrap_xcode_ui_testing.sh"
    fi

    if devtools_status="$("$EXEC_SH" --socket "$sock_path" --timeout 120 --argv /usr/sbin/DevToolsSecurity -status 2>&1)" &&
        [[ "$devtools_status" == *"enabled"* || "$devtools_status" == *"Enabled"* ]]; then
        check_ok "DevToolsSecurity is enabled"
    else
        check_warn "DevToolsSecurity could not be verified as enabled"
        if [[ -n "$devtools_status" ]]; then
            say "  DevToolsSecurity output: $devtools_status"
        fi
        say "  Fix: sudo /usr/sbin/DevToolsSecurity -enable"
    fi
fi

if [[ $missing -eq 0 ]]; then
    say "[guest-ready] OK"
    exit 0
fi

exit 1
