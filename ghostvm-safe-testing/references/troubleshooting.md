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
