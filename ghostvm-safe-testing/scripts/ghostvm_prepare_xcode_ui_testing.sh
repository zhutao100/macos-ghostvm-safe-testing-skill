#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/ghostvm_prepare_headless_automation.sh" \
    --snapshot xcode-ui-ready \
    --xcode-ui-testing \
    "$@"
