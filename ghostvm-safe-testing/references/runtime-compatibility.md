# Runtime Compatibility

## Select A Release

Keep the global CLI unchanged. Install a non-symlink wrapper into an owned directory and prepend it only for the selected session:

```bash
runtime_bin="$(mktemp -d)"
scripts/install_vmctl_wrapper.sh \
  --ghostvm-app /Applications/GhostVM-v3.app \
  --dest "$runtime_bin"
PATH="$runtime_bin:$PATH" scripts/ghostvm_doctor.sh --vm <Disposable> --snapshot automation-ready
```

Use the same `PATH` for preparation, safe runs, and guard restoration. Remove the owned wrapper directory after stopping the VM and restoring state. Per-bundle helper copies are refreshed by normal CLI start; an existing `Helper/` directory does not prove which release will execute.

Keep GhostTools paired with the intended release. Basic REST exec may work across versions without proving event delivery, port forwarding, file streaming, or WebSocket shell compatibility. Verify the guest application's actual bundle version after every replacement or snapshot revert.

## Clone A Prepared Snapshot

Copy the immutable prepared snapshot, not a user's current disk or saved memory. On the same APFS volume, use BSD copy-on-write copying and a unique bundle name:

```bash
source="$HOME/VMs/<Base>.GhostVM/Snapshots/<Prepared>"
disposable="$HOME/VMs/<Unique>.GhostVM"
test ! -e "$disposable" &&
  cp -cR "$source" "$disposable" &&
  vmctl discard-suspend "$disposable" &&
  vmctl snapshot "$disposable" create automation-ready
```

Snapshot revert also clears the suspended flag, but a snapshot directory copied directly into a bundle may still carry it. Normal start provisions the clone's helper. Do not start the copied hardware identity concurrently with its source; use the release's identity-regenerating clone operation for a separately provisioned long-lived VM.

## Prepared Snapshot Maintenance

Use this path only when the user explicitly requests replacement of an existing prepared snapshot. Ordinary evaluations still create uniquely named clones without changing the source snapshot.

1. Inventory the original with `doctor --no-start`. Record its stopped/suspended state, current config/identity checksums, disk and saved-memory metadata, and other snapshot metadata. A suspended original stays suspended throughout maintenance.
2. Clone only the prepared snapshot as above. Clear a stale suspended flag on the clone, pin the intended helper release, and keep test CPU/memory bounded. Do not regenerate hardware identity for a repair that will be published back to the same source.
3. Reproduce the missing capability in the clone. For Python, run `guest_ready --require-python <minimum>`; for UI workflows, use the prompt/Xcode readiness flags and verify real AppleEvents and an XCTest interaction. Application-specific tools/runtimes are not generic snapshot prerequisites unless the inventory claims them.
4. Repair only the demonstrated problem. Export evidence; remove task-owned test projects, added credentials, installer payloads, and temporary jobs. Preserve the existing account/profile. Stop the clone and restore its automation guard before creating a new stopped-state candidate snapshot.
5. Cold-boot the candidate through the safe runner and repeat the capability checks. Test successful and nonzero guest jobs, RO input/RW output, and guard restoration. A snapshot-create acknowledgement alone is not verification.
6. Preserve the old prepared snapshot under a unique backup directory outside the VM's `Snapshots/` directory. Stage the verified candidate separately, compare its hardware model/machine identity with the old snapshot, and retain the original snapshot's resource policy and persistent share settings. The candidate must have `isSuspended: false` and no session shares.
7. Replace only `Snapshots/<Prepared>` using same-filesystem directory renames, with rollback if publication fails. Never invoke source-bundle revert, discard-suspend, or start: those would affect the user's current disk/config/RAM rather than just the prepared recovery point. Preserve unrelated snapshots.
8. Cold-boot a disposable clone of the published snapshot, then stop it and restore its guard. Compare the recorded original current state and unrelated snapshots; document the exact tested OS/tool versions, results, and limitations without machine-specific paths or credentials.

Keep the previous snapshot backup until the user chooses its retention policy. Coarse snapshot replacement does not repair or migrate the original VM's suspended current session.

## macOS 27 Consent Stores

macOS 27.0.1 uses user TCC databases under:

```text
/private/var/containers/Data/ProtectedSystem/<container>/Data/Library/Application Support/com.apple.TCC/TCC.db
```

Identify TCC containers by `.com.apple.containermanagerd.metadata.plist`, `MCMMetadataIdentifier == com.apple.tccd`, and `MCMMetadataOwnership.uid`. Do not choose an arbitrary UUID directory, create a replacement legacy database, or assume a missing home-directory database means there is no user store.

Select a known guest account explicitly:

```bash
# Obtain this value inside the running disposable guest.
id -u <guest-user>

# Stop/revert only an owned disposable VM.
scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Disposable> \
  --base-snapshot <Base> \
  --snapshot automation-ready \
  --user <guest-user> \
  --user-uid <guest-uid> \
  --xcode-ui-testing
```

- Explicit UID selection scopes protected-store writes. Automatic discovery without a selected account includes user UIDs and root, not unrelated daemon accounts.
- Readable guest directory-service records can resolve selected names, but host permissions may prevent access even with ownership disabled. Pass `--user-uid` rather than inferring it from `stat`.
- Preserve unknown nullable/defaulted TCC columns; reject unknown required columns without defaults. macOS 27 adds a defaulted `reminder_count` column.
- Keep Xcode's Automation Mode and Developer Tools bootstrap separate from TCC rows.
- Verify a real AppleEvent and an XCTest launch/interaction, not just successful SQLite writes or snapshot creation.
- Resolve the default browser through the native application-handler API, not a private LaunchServices plist. Registration can enqueue multiple consent dialogs; even correct handler lookups do not prove those dialogs have closed. Check CoreServicesUIAgent and UserNotificationCenter windows before testing mouse input. Regenerating a clone's machine identity can surface guest account alerts; use an account-free automation image where possible and never alter the original VM's account state.

The stopped-guest seeding path works from a macOS 26 host against macOS 27 data volumes. This does not establish compatibility for every future APFS or TCC schema.

## Disk Formats

GhostVM v3 can create ASIF disks on macOS 26+ hosts. Both evaluated releases use `config.json` version 1 and the filename `disk.img`; the filename alone does not identify the disk format.

Verified on a macOS 26.7.1 host: v2.11.0 discovers and cold-boots a v3 ASIF bundle with schema version 1, including basic exec/clipboard with a v3 guest agent. V2 snapshot create/revert preserves ASIF and rolls guest contents back. V2's unchanged Virtualization.framework attachment supplies disk support even though its UI does not label ASIF. Do not generalize this to older hosts or conclude that v2 offers conversion or format-aware management.

ASIF migration creates a separate bundle and does not transfer the source snapshot history. Preserve the source and create new stopped-state snapshots after verifying the destination. Snapshot reverts copy the snapshot's image format and configuration, so keep RAW and ASIF recovery points unambiguous.

The offline seeder checks `disk.img` magic. RAW uses `hdiutil attach -owners off`; ASIF uses `diskutil image attach --mountOptions noowners` because the evaluated host's `hdiutil attach` rejects ASIF. Both consume plist entities; ASIF's bare `diskN` device entries are normalized before mount/eject. ASIF cleanup uses `diskutil eject`. Reverify mount discovery, selected consent stores, guest boot, and RO/RW shares after migration.

## State Boundaries

- Coarse GhostVM snapshots contain disk, configuration, hardware identity, and auxiliary storage, not saved guest RAM.
- A suspended configuration without `suspend.vzvmsave` is not a resumable snapshot. Clear the suspended flag only on the disposable clone before cold boot.
- Saved-memory restoration is a separate compatibility check from disk boot. Preserve the original saved state and never use a user's suspended VM for an evaluation.
- ASIF saved-memory resume did not reach readiness in mixed-version and same-v2 control attempts; the framework reported restore failure with "permission denied". This does not establish a cross-version incompatibility. Keep stopped-state recovery points and do not promise RAM portability.
- Guest snapshot rollback does not restore host helper defaults or writes through host shares.

## Transport Checks

- Host `/health` is liveness only. Parse the guest JSON `exitCode`; the raw CLI's process status is insufficient.
- The evaluated v3 beta waits for EOF on unframed empty HTTP requests. Send `Content-Length: 0` with raw-client GET requests. Its CLI half-closes the write side, so this regression may not appear through `vmctl remote health`.
- Both evaluated releases can block on pipe-sized REST output. V3's intended drains run behind the process wait on a serial queue. Redirect build/test logs to the writable artifact share; use the v3 PTY for streaming terminal interaction, not large REST responses.
- V3's raw guest connection has a fixed 30-second receive timeout independent of the requested exec timeout. A silent 32-second command with a 45-second exec deadline returned HTTP 500/`errno 35` after 30 seconds. The safe runner instead launches a detached guest job, polls the atomic exit artifact through the RW share, and bounds the job with its own deadline.
- Xcode preparation's root bootstrap still uses direct exec. If first-launch work exceeds the transport deadline, complete that bootstrap from inside the disposable guest or through the detached safe runner before repeating preparation. Increasing its exec timeout alone is insufficient.
- A transport deadline is not process-tree cancellation. Stop the disposable VM before reverting or restoring host settings.
- v3's WebSocket shell is useful for repeated terminal commands, but its CLI exit status must not be treated as the guest shell's exit status.
