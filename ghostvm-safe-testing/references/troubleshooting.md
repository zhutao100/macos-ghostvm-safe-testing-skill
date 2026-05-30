# Troubleshooting

This document is human-facing. Use it when the agent (or the included scripts) reports **ACTION REQUIRED (human)**.

## `vmctl` issues

### `vmctl` not found

Install a wrapper script named `vmctl` onto your `PATH`:

```bash
./ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app

command -v vmctl
vmctl --help
```

### `vmctl start ...` fails: `GhostVMHelper.app not found`

Typical error:

```text
Error: GhostVMHelper.app not found. Use --headless or run vmctl from within GhostVM.app.
```

**Cause:** GhostVM currently locates `GhostVMHelper.app` relative to the `vmctl` executable using `CommandLine.arguments[0]`. If `vmctl` is invoked as a bare name (argv0 = `vmctl`), or via a symlink in `PATH`, the helper lookup can fail.

**Fix (recommended):** use the wrapper installer instead of a symlink.

```bash
./ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app
```

**Workaround:** invoke the embedded `vmctl` by absolute path:

```bash
/Applications/GhostVM.app/Contents/PlugIns/Helpers/vmctl.app/Contents/MacOS/vmctl start ~/VMs/<Name>.GhostVM
```

## VM / snapshot issues

### Safe runner reports `RO share appears writable`

The runner edits `config.json` after snapshot revert and then probes the mounted
input share from inside the guest. If this fails:

1. Confirm the VM bundle's active `config.json` has `readOnly: true` for the
   `--ro` path.
2. If the input is a disposable staging copy, remove host write bits before
   starting the VM loop:

   ```bash
   chmod -R a-w /path/to/staged-input
   ```

3. Rerun once. Immediately after a VirtioFS mount appears, some host/guest
   combinations can briefly return inconsistent status from the Host API. The
   current runner retries and parses the probe exit code explicitly, but a
   second clean snapshot cycle is still the safest recovery if diagnostics show
   a real writable share.

### Temporary shared folders remain in VM settings

GhostVM persists shared folders in the VM bundle's `config.json`. If a run or manual setup added temp/ephemeral host paths, remove those entries from GhostVM settings or restore the previous `sharedFolders` configuration before reusing the VM.

If the desired settings are in a clean snapshot, stop the VM and revert that snapshot. If you edited `config.json` directly, stop the VM before editing and then restart it so the shared-folder mount reflects the restored settings.

### VM bundle not found

Default location is:

```text
~/VMs/<Name>.GhostVM
```

If you stored VMs elsewhere, pass `--bundle /absolute/path/to/<Name>.GhostVM` to the scripts.

### Snapshot missing

The safe runner expects a snapshot named `clean-state` by default.

Create it while the VM is stopped:

```bash
vmctl snapshot ~/VMs/<Name>.GhostVM create clean-state
```

## Offline privacy seeding issues

### The prep helper cannot identify the guest data volume

Typical symptom:

```text
ERROR: Could not identify the guest data volume after mounting disk.img.
```

**Fix path:** mount `disk.img` manually, identify the mounted APFS Data volume, then rerun the seeder directly.

```bash
hdiutil attach -nobrowse ~/VMs/<Name>.GhostVM/disk.img

# inspect volumes / mount points
diskutil list

ghostvm-safe-testing/scripts/ghostvm_guest_privacy_seed.py \
  --mounted-root /Volumes/<Mounted Data Volume>
```

The mounted root you pass should contain paths like:

```text
/Volumes/<Mounted Data Volume>/Library
/Volumes/<Mounted Data Volume>/Users
/Volumes/<Mounted Data Volume>/private
```

### The helper says the system `TCC.db` is missing

Typical symptom:

```text
ERROR: System TCC.db not found at .../Library/Application Support/com.apple.TCC/TCC.db
```

That usually means the path passed to `--mounted-root` is not the guest data-volume root.

Re-check the mount point and rerun with the correct root.

### You need to seed a different sender binary or AppleEvents receiver

If automation still prompts after snapshot preparation, the most common cause is an identity mismatch between the seeded baseline and the real requester.

Extend the prep command instead of patching the guest manually each time:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --tcc-client /absolute/path/to/requester \
  --appleevent-target com.apple.TargetApp
```

If `automation-ready` already exists, the helper fails without deleting it. Use a new snapshot name, or pass `--replace-snapshot` only after the user explicitly approves overwriting that snapshot.

### You only want to patch specific guest users

Use repeatable `--user` flags:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --user agent   --user root
```


## Xcode/XCTest UI testing still prompts

### “XCTest is trying to Enable UI Automation” or timeout while enabling automation mode

This is the XCTest Automation Mode authentication gate. It is not fixed by editing `TCC.db` alone.

Preferred disposable-VM fix:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --user agent
```

If the helper cannot run sudo noninteractively, either pass the known disposable-guest password for that host invocation only:

```bash
GHOSTVM_GUEST_SUDO_PASSWORD='<guest-password>' \
  ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
    --vm <Name> \
    --base-snapshot clean-state \
    --user agent
```

or boot the disposable guest once and run:

```bash
sudo /Users/Shared/ghostvm-safe-testing/ghostvm_guest_bootstrap_xcode_ui_testing.sh \
  --xcode-app /Applications/Xcode.app \
  --user agent
```

Then stop the VM and create the prepared snapshot. Do not persist guest passwords in repo files.

### Accessibility prompt for Xcode Helper

If a UI test still prompts for Accessibility, verify that the actual requester identity matches what was seeded. The common nested helper is:

```text
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/Xcode Helper.app
```

Use a non-default Xcode path when needed:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --xcode-app /Applications/Xcode-16.4.app
```

Verify readiness with:

```bash
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

## Host API / GhostTools issues

### Host API socket not found

The Host API socket is created by **GhostVMHelper.app** on the host and lives at:

```text
~/Library/Application Support/GhostVM/api/<VMName>.GhostVM.sock
```

Common causes:

- VM started in `--headless` mode (Host API is not available).
- GhostVMHelper.app did not launch (vmctl helper lookup problem; see above).

### `/health` fails (GhostTools not reachable)

GhostVM proxies Host API requests over vsock to GhostTools inside the guest.

Common causes:

- GhostTools not installed in the guest.
- GhostTools not running (not added to Login Items, user not logged in, auto-login disabled).

Fix checklist:

1. Boot the VM interactively.
2. Install `GhostTools.dmg` from:

   ```text
   /Applications/GhostVM.app/Contents/Resources/GhostTools.dmg
   ```

3. Move `GhostTools.app` into the guest’s `/Applications`.
4. Add it to **System Settings → General → Login Items**.
5. Reboot the VM and confirm the GhostTools menu bar icon appears.

## `exec` fails (but `health` / `apps` / clipboard work)

See `references/remote-exec.md`.

## Local Network still blocks traffic

### The traffic is outside your seeded CIDRs

The offline helper only exempts the CIDRs you seed. If your workflow talks to other ranges, either:

- add those ranges with repeatable `--cidr`
- run the traffic from an auto-allowed context inside the guest (root daemon / Terminal / SSH)
- or prime the Local Network prompt once and snapshot the result

Example:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --cidr 100.64.0.0/10 \
  --cidr fd00:1234::/64
```

### You need to verify what was seeded

Mount the guest image and inspect the Local Network preferences file:

```bash
plutil -p /Volumes/<Mounted Data Volume>/private/var/root/Library/Preferences/com.apple.network.local-network.plist
```

Check for:

- `AllowedEthernetLocalNetworkAddresses`
- `AllowedWiFiLocalNetworkAddresses`

### You need a clean first-run retry

Do not try to “reset” Local Network state in place. Revert to a clean snapshot instead.
