# Headless automation gating in a GhostVM guest (TCC + Xcode UI testing + Local Network)

This note documents the **pragmatic preparation path** for disposable GhostVM guests that must run unattended automation without stopping on first-use privacy prompts.

The relevant categories are:

- **Classic TCC services** used by UI / AppleScript automation, such as AppleEvents, Accessibility, Screen Recording, and synthetic input.
- **Xcode/XCTest UI testing gates**, especially `Xcode Helper.app` Accessibility and the separate XCTest Automation Mode authentication gate.
- **Local Network privacy**, which is a different subsystem and does not behave like classic TCC.

For this skill, the default operating model is:

> Start from a clean snapshot, mutate the stopped guest image from the host, and then snapshot the prepared result.

That matches GhostVM’s coarse-grained snapshot model and keeps the resulting state reproducible.

---

## 1) Preparation strategy matrix

| Problem | Preferred path for disposable GhostVM guests | Why |
| --- | --- | --- |
| Baseline AppleScript / UI automation permissions | Offline-seed `TCC.db` while the VM is stopped | Deterministic, snapshot-friendly, and avoids guest-live setup friction |
| Xcode/XCTest macOS UI tests | Use `--xcode-ui-testing`: offline-seed Xcode-related TCC clients, then run a guest-root bootstrap for Automation Mode / Developer Tools / Xcode first launch | The XCTest Automation Mode auth gate is not solved by TCC rows alone |
| Baseline Local Network access for known automation subnets | Offline-seed `com.apple.network.local-network` CIDR defaults while the VM is stopped | Avoids per-app prompts for those ranges |
| Safari DOM automation with AppleScript `do JavaScript` | Offline-seed Safari's `AllowJavaScriptFromAppleEvents` preference for guest users | TCC AppleEvents grants alone do not enable Safari's app-level JavaScript gate |
| Extra AppleEvents receivers or sender binaries | Extend the offline seed (`--appleevent-target`, `--tcc-client`) | Keeps prep reproducible |
| Traffic outside the seeded Local Network CIDRs | Use a root launch daemon / Terminal / SSH context inside the guest, or do one-time interactive priming and snapshot the result | Local Network is not solved by `TCC.db` edits |
| Re-testing “first run” behavior | Revert to a clean snapshot | Local Network state is not cleanly resettable in place |

---

## 2) Xcode/XCTest UI testing prompts

### What the prompt represents

macOS UI tests launched by Xcode/XCTest interact with the system through Accessibility-style UI automation. In practice, disposable VM preparation has to cover two independent gates:

1. **TCC / Accessibility identity:** the relevant Xcode components need permission. The most important nested app is typically:

   ```text
   /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/Xcode Helper.app
   ```

2. **XCTest Automation Mode:** modern XCTest can stop at an authentication prompt while enabling UI Automation. This is controlled by `automationmodetool`, not by the classic `TCC.db` rows.

### Disposable-VM solution

Use a dedicated prepared snapshot:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot xcode-ui-ready \
  --xcode-ui-testing \
  --user agent
```

If `xcode-ui-ready` already exists, the helper stops before changing VM state. Pick a new snapshot name, or pass `--replace-snapshot` only after the user explicitly asks to overwrite that snapshot.

That path does both halves:

- **Offline seed while stopped:** adds common Xcode UI-testing TCC candidates when they exist in the guest image:
  - `Xcode.app` bundle id and executable
  - nested `Xcode Helper.app` bundle id and executable
  - Xcode, `xcodebuild`, and `xcrun` candidates
  - Xcode-specific `kTCCServiceDeveloperTool` and `kTCCServiceListenEvent` services
- **Guest bootstrap after boot:** runs `ghostvm_guest_bootstrap_xcode_ui_testing.sh` as root to:
  - select the requested Xcode developer directory
  - accept the Xcode license and run first-launch setup
  - enable `DevToolsSecurity`
  - add selected users to `_developer`
  - run `automationmodetool enable-automationmode-without-authentication`

The guest bootstrap uses noninteractive sudo from the Host API path. For fully unattended snapshot preparation, configure temporary passwordless sudo for the disposable automation user. For disposable guests with a known password, set `GHOSTVM_GUEST_SUDO_PASSWORD` for that host invocation only:

```bash
GHOSTVM_GUEST_SUDO_PASSWORD='<guest-password>' \
  ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
    --vm <Name> \
    --base-snapshot clean-state \
    --user agent
```

If neither is available, run the bootstrap script manually once inside the guest with `sudo` and then snapshot the result. Do not persist guest passwords in repo files.

Verify the prepared state with:

```bash
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

### What not to assume

Do not assume that adding `Xcode Helper.app` to Accessibility alone fixes the whole class. If the run fails with messages like “Timed out while enabling automation mode” or “XCTest is trying to Enable UI Automation,” run or verify the Automation Mode bootstrap.

---

## 3) Local Network privacy

### What matters operationally

Local Network privacy is **not classic TCC**. Treat it as a separate networking/privacy policy with different reset and attribution behavior.

For disposable automation VMs, the highest-leverage path is to configure **allowed CIDR ranges** inside the guest image before boot. The helper writes the root-domain preferences file:

```text
/private/var/root/Library/Preferences/com.apple.network.local-network.plist
```

with keys such as:

- `AllowedEthernetLocalNetworkAddresses`
- `AllowedWiFiLocalNetworkAddresses`

This makes those network ranges behave as non-local for the guest’s Local Network checks.

### Default CIDRs used by the helper

If you do not pass explicit `--cidr` values, the helper seeds these common automation/lab ranges:

- `10.0.0.0/8`
- `172.16.0.0/12`
- `192.168.0.0/16`
- `169.254.0.0/16`
- `fc00::/7`
- `fe80::/10`

Adjust them if your automation network is narrower.

### What Local Network seeding does **not** solve

If your workflow touches addresses outside the seeded CIDRs, you still need one of these patterns:

1. **Run that traffic from an auto-allowed execution context** inside the guest, such as:
   - a root `launchd` daemon
   - a CLI run from Terminal
   - a CLI run over SSH
2. **Prime the prompt interactively once**, then snapshot the result.

Do **not** try to solve Local Network privacy by editing `TCC.db`; it is the wrong lever for that subsystem.

---

## 4) Classic TCC services for automation

### Default seeded baseline

`ghostvm_guest_privacy_seed.py` patches the stopped guest’s:

- system database
  - `/Library/Application Support/com.apple.TCC/TCC.db`
- detected per-user databases
  - `/Users/<name>/Library/Application Support/com.apple.TCC/TCC.db`
  - `/private/var/root/Library/Application Support/com.apple.TCC/TCC.db`

The default seeded rows cover these sender binaries:

- `/usr/bin/osascript`
- `/usr/libexec/sshd-keygen-wrapper`

for these services:

- `kTCCServiceAccessibility`
- `kTCCServiceScreenCapture`
- `kTCCServicePostEvent`
- `kTCCServiceAppleEvents`

When `--xcode-ui-testing` is enabled, the helper also adds:

- `kTCCServiceDeveloperTool`
- `kTCCServiceListenEvent`

and these default AppleEvents receivers:

- `com.apple.systemevents`
- `com.apple.finder`
- `com.apple.Safari`

Safari has an additional app-level setting for DOM JavaScript via Apple Events. The offline seed enables `AllowJavaScriptFromAppleEvents` in the Safari container preferences for detected guest users by default. Pass `--skip-safari-js-apple-events` when the guest snapshot should leave that Safari setting unchanged.

### Extending the baseline

If your actual requester differs, extend the seed explicitly instead of relying on ad hoc guest edits.

Examples:

```bash
# extra AppleEvents receiver
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --appleevent-target com.apple.TextEdit

# extra sender binary
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --tcc-client /usr/local/bin/cliclick

# extra service found from attribution
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --tcc-service kTCCServiceListenEvent
```

If a permission is user-specific and you do not want auto-detection, narrow the patch to a specific guest account:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --user agent
```

---

## 5) Recommended workflow

### Pure offline preparation

Use this when the seeded baseline is sufficient:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready
```

That does:

1. stop the VM if needed
2. revert `clean-state` if requested
3. mount `disk.img` from the host
4. seed Local Network defaults, Safari's JavaScript-from-Apple-Events preference, and baseline TCC rows
5. detach the image
6. create `automation-ready`; if it already exists, fail without deleting it unless `--replace-snapshot` was passed by explicit user instruction

For Xcode UI testing, use the dedicated option:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --user agent
```

### Offline preparation plus priming

Use this only when you intentionally need a prompt that is outside the seeded baseline:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready   --prime-automation   --prime-local-network
```

The priming step boots the guest, exercises the relevant operation, and expects a human to click through any guest-visible prompt. The resulting state is then captured in the new snapshot.

Do not delete an existing prepared snapshot as a convenience. Use a new snapshot name by default, and use `--replace-snapshot` only when the user explicitly approves deleting and recreating the target snapshot.

---

## 6) More useful disposable-VM headless setups

These setups are valuable because they move interactive gating into a golden snapshot instead of letting it interrupt agent runs.

| Setup | Why it helps | Where this skill handles it |
| --- | --- | --- |
| Auto-login for the automation user | GhostTools and GUI-session automation need a logged-in user session | Human VM provisioning; checked indirectly by `/health` |
| GhostTools as a Login Item | Provides Host API `/health` and `exec` after boot | Human VM provisioning; doctor/ready scripts diagnose failures |
| Xcode license + first launch completed | Avoids first-use dialogs or command-line setup stalls | `ghostvm_guest_bootstrap_xcode_ui_testing.sh` |
| `automationmodetool` configured | Avoids XCTest Automation Mode authentication prompt | `--xcode-ui-testing` guest bootstrap |
| `DevToolsSecurity -enable` + `_developer` membership | Avoids developer-tool authorization prompts for admin/developer users | `--xcode-ui-testing` guest bootstrap |
| Safari JavaScript from Apple Events enabled | Allows Safari DOM automation through Apple Events | offline Safari preference seed |
| Local Network CIDR exemptions | Avoids local-network prompts for lab/private subnets | offline Local Network preference seed |
| Purpose-built snapshots (`clean-state`, `automation-ready`, `xcode-ui-ready`) | Gives deterministic rollback and separates consent profiles | `ghostvm_prepare_headless_automation.sh` |
| No disk encryption in disposable guests | Makes offline disk mutation viable | VM design assumption; do not copy to real hosts |

---

## 7) Why the skill defaults to offline seeding

For disposable GhostVM images, offline mutation is the most reliable boundary because:

- the host already has full control over the VM bundle
- the guest disk is usually an unencrypted APFS image
- snapshots give you a clean rollback path
- repeated in-guest setup is less reproducible and more brittle

That is why `ghostvm_prepare_headless_automation.sh` now treats the stopped guest image as the default preparation target and uses interactive priming only as a fallback.
