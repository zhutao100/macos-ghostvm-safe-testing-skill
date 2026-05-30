#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_guest_ready.sh --vm <Name> [--bundle /path/to/<Name>.GhostVM] [--root ~/VMs] [--require-rosetta] [--require-ghosttools-prompts-clear] [--require-xcode-ui-testing]

Checks (inside the guest, via Host API):
  - GhostTools /health
  - Xcode Command Line Tools present (xcode-select -p)
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
    action_required "Host API reachable, but GhostTools /health failed.\nEnsure GhostTools is installed + running in the guest (auto-login + Login Items)."
fi

REMOTE_EXEC_PY="$(dirname "$0")/ghostvm_remote_exec.py"

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

# Xcode CLT presence.
if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/usr/bin/xcode-select -p >/dev/null 2>&1" >/dev/null 2>&1; then
    check_ok "Xcode Command Line Tools detected (xcode-select -p)"
else
    check_warn "Xcode Command Line Tools missing"
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
ls_plist="$HOME/Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
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
  [[ -f "$ls_plist" ]] || return 1
  /usr/bin/plutil -p "$ls_plist" 2>/dev/null | /usr/bin/awk -v scheme="$scheme" -v domain="$domain" '
    /^  [0-9]+ => \{/ { entry_scheme = 0; entry_handler = 0 }
    /LSHandlerURLScheme/ && $0 ~ "\"" scheme "\"" { entry_scheme = 1 }
    /LSHandlerRoleAll/ && $0 ~ domain { entry_handler = 1 }
    /^  }/ {
      if (entry_scheme && entry_handler) { found = 1 }
      entry_scheme = 0
      entry_handler = 0
    }
    END { exit found ? 0 : 1 }
  '
}

for scheme in http https; do
  if scheme_handler_ok "$scheme"; then
    echo "[OK] GhostTools is default handler for $scheme"
  else
    echo "[MISSING] GhostTools is not the default handler for $scheme"
    missing=1
  fi
done

exit "$missing"
GUESTSH
    )"

    if ghosttools_prompt_report="$(python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "$ghosttools_prompt_check" 2>&1)"; then
        while IFS= read -r line; do
            [[ -n "$line" ]] && say "$line"
        done <<<"$ghosttools_prompt_report"
        check_ok "GhostTools setup-window prerequisites are satisfied"
    else
        check_warn "GhostTools setup-window prerequisites are incomplete"
        if [[ -n "$ghosttools_prompt_report" ]]; then
            printf '%s
' "$ghosttools_prompt_report" | sed 's/^/  /' >&2
        fi
        say "  Fix: inside the disposable guest, run GhostTools from /Applications once, enable Auto Start and Auto Update, set it as the default browser, answer the notification prompt, then snapshot."
        say "  Note: default-browser readiness prevents GhostTools Setup and URL forwarding prompts; notification authorization may still require one interactive approval on fresh macOS installs."
    fi
fi

if [[ $REQUIRE_XCODE_UI_TESTING -eq 1 ]]; then
    if python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/usr/bin/xcodebuild -version >/dev/null 2>&1" >/dev/null 2>&1; then
        check_ok "xcodebuild is usable"
    else
        check_warn "xcodebuild is not usable"
        say "  Fix: install/select Xcode, then run scripts/ghostvm_prepare_headless_automation.sh --xcode-ui-testing"
    fi

    automation_status="$(python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "command -v automationmodetool >/dev/null 2>&1 && automationmodetool 2>&1" 2>/dev/null || true)"
    if [[ "$automation_status" == *"DOES NOT REQUIRE"* ]]; then
        check_ok "Automation Mode does not require user authentication"
    else
        check_warn "Automation Mode still requires user authentication or automationmodetool is unavailable"
        if [[ -n "$automation_status" ]]; then
            say "  automationmodetool output:"
            printf '%s
' "$automation_status" | sed 's/^/    /' >&2
        fi
        say "  Fix: run scripts/ghostvm_prepare_xcode_ui_testing.sh, optionally with GHOSTVM_GUEST_SUDO_PASSWORD for disposable guests, or inside the guest run:"
        say "    sudo /Users/Shared/ghostvm-safe-testing/ghostvm_guest_bootstrap_xcode_ui_testing.sh"
    fi

    devtools_status="$(python3 "$REMOTE_EXEC_PY" --socket "$sock_path" /bin/zsh -lc "/usr/sbin/DevToolsSecurity -status 2>&1" 2>/dev/null || true)"
    if [[ "$devtools_status" == *"enabled"* || "$devtools_status" == *"Enabled"* ]]; then
        check_ok "DevToolsSecurity is enabled"
    else
        check_warn "DevToolsSecurity is not enabled"
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
