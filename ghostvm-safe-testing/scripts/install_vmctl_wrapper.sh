#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  install_vmctl_wrapper.sh [--ghostvm-app /Applications/GhostVM.app] [--dest DIR] [--force]

Installs a small `vmctl` wrapper script into a directory on PATH.

Why a wrapper (not a symlink)?
- GhostVM currently finds GhostVMHelper.app relative to CommandLine.arguments[0].
- If `vmctl` is invoked as a bare name (argv0 = vmctl), helper lookup can fail.
- This wrapper `exec`s the embedded vmctl by absolute path, so argv0 is always absolute.

Options:
  --ghostvm-app PATH   Path to GhostVM.app (default: /Applications/GhostVM.app)
  --dest DIR           Install directory (default: pick first writable of /usr/local/bin, /opt/homebrew/bin, ~/.local/bin)
  --force              Overwrite existing DEST/vmctl
  -h, --help           Show help
USAGE
}

die() {
    echo "error: $*" >&2
    exit 1
}

GHOSTVM_APP="/Applications/GhostVM.app"
DEST_DIR=""
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ghostvm-app)
            [[ $# -ge 2 ]] || die "--ghostvm-app requires a value"
            GHOSTVM_APP="$2"
            shift 2
            ;;
        --dest)
            [[ $# -ge 2 ]] || die "--dest requires a value"
            DEST_DIR="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (use --help)"
            ;;
    esac
done

VMCTL_REAL="$GHOSTVM_APP/Contents/PlugIns/Helpers/vmctl.app/Contents/MacOS/vmctl"
[[ -x "$VMCTL_REAL" ]] || die "embedded vmctl not found or not executable: $VMCTL_REAL"

pick_dest() {
    local d
    for d in /usr/local/bin /opt/homebrew/bin "$HOME/.local/bin"; do
        if [[ -d "$d" ]] && [[ -w "$d" ]]; then
            echo "$d"
            return 0
        fi
    done
    echo "$HOME/.local/bin"
}

if [[ -z "$DEST_DIR" ]]; then
    DEST_DIR="$(pick_dest)"
fi

# Expand ~ and normalize.
DEST_DIR="$(
    python3 - <<PY
import os, sys
p = os.path.expanduser(sys.argv[1])
print(os.path.abspath(p))
PY
    "$DEST_DIR"
)"

mkdir -p "$DEST_DIR" 2>/dev/null || {
    echo "error: failed to create DEST dir: $DEST_DIR" >&2
    echo "hint: try running with sudo, or choose a user-writable dir like ~/.local/bin" >&2
    exit 1
}

[[ -w "$DEST_DIR" ]] || {
    echo "error: DEST dir is not writable: $DEST_DIR" >&2
    echo "hint: try running with sudo, or choose --dest ~/.local/bin" >&2
    exit 1
}

TARGET="$DEST_DIR/vmctl"
if [[ -e "$TARGET" && $FORCE -ne 1 ]]; then
    die "$TARGET already exists (use --force to overwrite)"
fi

TMP="$(mktemp -t vmctl-wrapper.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

cat >"$TMP" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
VMCTL_REAL='$VMCTL_REAL'
exec "\$VMCTL_REAL" "\$@"
WRAPPER

chmod 0755 "$TMP"

mv -f "$TMP" "$TARGET"
trap - EXIT

if [[ ":$PATH:" != *":$DEST_DIR:"* ]]; then
    echo "installed: $TARGET" >&2
    echo "warning: $DEST_DIR is not on PATH for the current shell" >&2
    echo "hint: add this to your shell profile:" >&2
    echo "  export PATH=\"$DEST_DIR:\$PATH\"" >&2
else
    echo "installed: $TARGET" >&2
fi

echo "verify:" >&2
echo "  command -v vmctl" >&2
echo "  vmctl --help" >&2
