# Headless automation gating in a GhostVM guest (TCC + Local Network)

This note documents the **pragmatic preparation path** for disposable GhostVM guests that must run unattended automation without stopping on first-use privacy prompts.

The relevant categories are:

- **Classic TCC services** used by UI / AppleScript automation, such as AppleEvents, Accessibility, Screen Recording, and synthetic input.
- **Local Network privacy**, which is a different subsystem and does not behave like classic TCC.

For this skill, the default operating model is:

> Start from a clean snapshot, mutate the stopped guest image from the host, and then snapshot the prepared result.

That matches GhostVM’s coarse-grained snapshot model and keeps the resulting state reproducible.

---

## 1) Preparation strategy matrix

| Problem | Preferred path for disposable GhostVM guests | Why |
| --- | --- | --- |
| Baseline AppleScript / UI automation permissions | Offline-seed `TCC.db` while the VM is stopped | Deterministic, snapshot-friendly, and avoids guest-live setup friction |
| Baseline Local Network access for known automation subnets | Offline-seed `com.apple.network.local-network` CIDR defaults while the VM is stopped | Avoids per-app prompts for those ranges |
| Safari DOM automation with AppleScript `do JavaScript` | Offline-seed Safari's `AllowJavaScriptFromAppleEvents` preference for guest users | TCC AppleEvents grants alone do not enable Safari's app-level JavaScript gate |
| Extra AppleEvents receivers or sender binaries | Extend the offline seed (`--appleevent-target`, `--tcc-client`) | Keeps prep reproducible |
| Traffic outside the seeded Local Network CIDRs | Use a root launch daemon / Terminal / SSH context inside the guest, or do one-time interactive priming and snapshot the result | Local Network is not solved by `TCC.db` edits |
| Re-testing “first run” behavior | Revert to a clean snapshot | Local Network state is not cleanly resettable in place |

---

## 2) Local Network privacy

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

## 3) Classic TCC services for automation

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
```

If a permission is user-specific and you do not want auto-detection, narrow the patch to a specific guest account:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --user agent
```

---

## 4) Recommended workflow

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
6. create `automation-ready`

### Offline preparation plus priming

Use this only when you intentionally need a prompt that is outside the seeded baseline:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready   --prime-automation   --prime-local-network
```

The priming step boots the guest, exercises the relevant operation, and expects a human to click through any guest-visible prompt. The resulting state is then captured in the new snapshot.

---

## 5) Why the skill defaults to offline seeding

For disposable GhostVM images, offline mutation is the most reliable boundary because:

- the host already has full control over the VM bundle
- the guest disk is usually an unencrypted APFS image
- snapshots give you a clean rollback path
- repeated in-guest setup is less reproducible and more brittle

That is why `ghostvm_prepare_headless_automation.sh` now treats the stopped guest image as the default preparation target and uses interactive priming only as a fallback.
