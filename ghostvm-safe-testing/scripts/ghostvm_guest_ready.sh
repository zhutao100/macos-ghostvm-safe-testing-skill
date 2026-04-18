#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ghostvm_guest_ready.sh --vm <Name> [--bundle /path/to/<Name>.GhostVM] [--root ~/VMs] [--require-rosetta]

Checks (inside the guest, via Host API):
  - GhostTools /health
  - Xcode Command Line Tools present (xcode-select -p)
  - (optional) Rosetta present (pkgutil receipt) when --require-rosetta is set

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

if [[ $missing -eq 0 ]]; then
    say "[guest-ready] OK"
    exit 0
fi

exit 1
