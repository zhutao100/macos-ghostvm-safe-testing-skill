# Automation tuning reference

This reference documents the non-TCC/non-Local-Network tuning used by `scripts/ghostvm_guest_tune_automation.py` and the host-side guards used by `scripts/ghostvm_automation_guard.py`.

## Guest-side vanilla macOS tuning

Run path:

```bash
scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready
```

The prep script invokes `ghostvm_guest_tune_automation.py` by default while the VM is stopped.

| Area | Mechanism | Why it matters for disposable VM automation | Escape hatch |
|---|---|---|---|
| macOS update automation | Writes `/Library/Preferences/com.apple.SoftwareUpdate.plist` with automatic checks/downloads/installs disabled | Avoids background downloads, install prompts, CPU/network churn, and unexpected snapshot drift | `--skip-automation-tuning` |
| Security Responses/system data | Disabled by default through Software Update preference keys | Keeps disposable snapshots stationary and avoids background install variance | `--keep-security-responses` |
| App Store auto updates | Writes `/Library/Preferences/com.apple.commerce.plist` with app auto updates disabled | Avoids background app changes during tests | `--skip-automation-tuning` |
| Timed lock/screen saver | Writes per-user `~/Library/Preferences/com.apple.screensaver.plist` and root screensaver defaults | Prevents remote exec/UI automation from landing on a locked session after idle time | `--skip-automation-tuning` or `ghostvm_guest_tune_automation.py --skip-screen-lock` |
| Time Machine prompts | Writes `/Library/Preferences/com.apple.TimeMachine.plist` | Suppresses new-disk backup prompts when transient volumes appear | `ghostvm_guest_tune_automation.py --skip-time-machine` |
| Spotlight indexing | Creates `.metadata_never_index` on the data volume | Reduces indexing CPU and disk churn in disposable snapshots | `ghostvm_guest_tune_automation.py --skip-spotlight` |

Notes:

- These are image-prep defaults for throwaway automation guests, not recommendations for a primary human workstation.
- Offline plist edits are intentionally conservative. If a particular macOS release ignores one preference key, the corresponding System Settings state can still be verified once and captured in the prepared snapshot.
- Host-side `caffeinate` is still useful for keeping the physical host awake while a long VM run is active, but it is not a substitute for guest idle/lock tuning.
- Research anchors:
  - Software Update automatic download/install controls: <https://support.apple.com/guide/mac-help/software-update-settings-on-mac-mchla7037245/mac>
  - macOS 15+ declarative update settings map automatic actions to `Download`, `InstallOSUpdates`, and `InstallSecurityUpdates`: <https://support.apple.com/guide/deployment/software-update-settings-declarative-dep0578d8b8a/1/web/1.0>
  - Lock Screen idle password behavior: <https://support.apple.com/en-gb/guide/mac-help/mh11784/mac>
  - Time Machine new-disk prompts and automatic/local backup behavior: <https://support.apple.com/guide/mac-help/connect-a-new-backup-disk-mh11430/mac> and <https://support.apple.com/en-euro/guide/deployment/dep1cddddk7/web>
  - Spotlight indexing is volume scoped; `mdutil -i off` is the live-system equivalent of the offline opt-out intent: <https://keith.github.io/xcode-man-pages/mdutil.1.html>

## Host-side GhostVM automation guards

`ghostvm_safe_test.sh` uses `ghostvm_automation_guard.py apply` after snapshot revert and before shared-folder configuration. It saves a state JSON before changing settings, so restore removes run-specific shares and returns the VM to its pre-run config.

`ghostvm_prepare_headless_automation.sh` also applies the guard before any GhostVMHelper boot and while creating prepared snapshots. Prepared snapshots are created while the sanitized config is active; the current VM config/defaults are restored afterward unless `--keep-running` intentionally leaves the VM active for follow-up work.

Applied settings:

| Guard | Mechanism | Failure avoided |
|---|---|---|
| Missing shared folders | The guard clears stale stored shares before `ghostvm_configure_shares.py` writes existing RO/RW directories | GhostVMHelper `Shared Folder Not Found` alert requiring `Boot Anyway` |
| Bridged networking | Temporarily writes `networkConfig = {mode: nat, bridgeInterfaceIdentifier: null}` | GhostVM builder error when bridged mode lacks a selected interface |
| Port forwards | Temporarily clears `portForwards` | Host-port conflicts and port-forward popovers/noise |
| Helper auto port map | Writes host default `autoPortMap_<stableHash(bundle-path)> = false` for `org.ghostvm.ghostvm.helper` | Toolbar popover/noise from automatic guest-port detection |
| Clipboard sync | Writes host default `clipboardSyncMode_<stableHash(bundle-path)> = disabled` | Clipboard permission popovers or focus theft |
| URL auto-open | Writes host default `openURLsAutomatically_<stableHash(bundle-path)> = false` | Avoids automatic host URL opening from guest requests; guest-initiated URL opens can still create a helper URL-permission popover that requires explicit action |

## Guest-side GhostTools prompt readiness

GhostTools can show guest-side setup/notification UI on launch. Before snapshotting UI-automation guests, run:

```bash
scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear
```

The check covers the source-backed setup-window gates: `/Applications/GhostTools.app`, the LaunchAgent plist, `org.ghostvm.ghosttools.autoStartEnabled`, `org.ghostvm.ghosttools.autoUpdateEnabled`, and `http`/`https` default-handler registration. Notification authorization is a macOS user prompt; answer it during one interactive GhostTools launch before taking the prepared snapshot.

Restore after a kept-running session:

```bash
python3 scripts/ghostvm_automation_guard.py restore \
  --state /path/to/ghostvm-runs/<Name>/<run-id>/automation_state.json \
  --stop-vm
```

Inspect only:

```bash
python3 scripts/ghostvm_automation_guard.py inspect --bundle /path/to/<Name>.GhostVM
```

## Stable-hash detail

GhostVM helper stores per-VM defaults under keys derived from `bundleURL.path.stableHash`. The guard implements the same UInt64 DJB2 algorithm used by GhostVMKit:

```text
h = 5381
for byte in utf8(path): h = h * 33 + byte, wrapping to UInt64
```

Known test vectors:

```text
"" -> 5381
"hello" -> 210714636441
"/path/to/vm" -> 13789981918659725477
"GhostVM" -> 229425741865133
```
