# Guest OS Upgrades

## Boundaries

- Run Software Update or the full macOS installer inside an owned guest clone. Preserve its apps, account, data, hardware model, machine identifier, disk format, and paired GhostTools release.
- `vmctl install` invokes IPSW restore installation, not a data-preserving upgrade. Do not run it against an existing environment. An `install --restore-image` option is not implemented by the evaluated v2 CLI.
- Updating `restoreImagePath` or `lastInstallVersion` is not an upgrade. GhostVM's displayed installation metadata can remain stale; the running guest's `sw_vers` is authoritative.
- Preserve the source VM's current disk and saved memory by default. For requested prepared-snapshot replacement, follow [Prepared Snapshot Maintenance](runtime-compatibility.md#prepared-snapshot-maintenance), including backups outside active snapshot discovery and a cold boot of the published replacement. An explicit whole-VM replacement with no retained backups uses [Whole-VM Consolidation](#whole-vm-consolidation) instead.
- Do not use `ghostvm_safe_test.sh` to perform an OS upgrade: its initial revert and deadline cleanup conflict with installer restarts. Use it for verification after installation.
- Do not upgrade the host as an implicit remedy for a guest failure.

## Select And Preflight

1. Verify the latest public release separately for each requested major version against Apple's release information and the guest's current catalog. Record an exact version/build pair; do not select an offered major upgrade when maintaining an older major.
2. Pin the intended runtime using a private `vmctl` wrapper. Run `doctor --no-start` against the original, capture its current-state and unrelated-snapshot fingerprints, and clone only the prepared snapshot.
3. Boot the clone normally with guarded NAT networking and bounded CPU/RAM. Keep the host awake and network access available during installation, including with a cached full installer.
4. Record guest `sw_vers`, `id -u`, available startup/data-volume space, volume ownership, secure-token status, and memory before installation. Use `sudo diskutil apfs listUsers /` for volume-owner diagnosis; `sudo` alone does not establish installer authorization.
5. Create a stopped-state recovery snapshot before starting the upgrade. Disk, auxiliary storage, config, and platform identity belong to the same recovery point.

```bash
python3 scripts/ghostvm_guest_os_check.py \
  --bundle "$HOME/VMs/<OwnedClone>.GhostVM"
```

This read-only helper checks guest version/build and configured versus reported memory. Its default 80% usable-memory threshold is a conservative diagnostic policy, not an Apple specification. An unavailable `hw.memsize_usable` is reported as an unverified check, not evidence of healthy usable RAM. Record a pre-existing memory failure rather than attributing it to the new attempt.

Automation tuning disables automatic update preferences, not manual installation. `--skip-automation-tuning` and `--keep-security-responses` do not re-enable previously disabled settings.

## Install Inside The Guest

Software Update can install the exact label returned by `softwareupdate --list`:

```bash
# GUEST
sudo /usr/sbin/softwareupdate --install "<exact same-major label>" --restart
```

For a cached full installer, verify its Apple signature and deploy the installer app:

```bash
# GUEST; package path must be accessible inside the guest.
/usr/sbin/pkgutil --check-signature "/path/to/InstallAssistant.pkg"
sudo /usr/sbin/installer -pkg "/path/to/InstallAssistant.pkg" -target /
"/Applications/Install macOS <Name>.app/Contents/Resources/startosinstall" --usage
```

The package operation installs an app. Its message `The upgrade was successful` does not establish an OS upgrade. Inspect the installed payload's version/build and run the actual installer, using only options present in its bundled usage:

Discover the installed app rather than constructing its name from the package filename or OS codename. The evaluated 27.2 package installs `Install macOS 27 Golden Gate.app`, not `Install macOS Golden Gate.app`.

```bash
# GUEST, invoked by the existing administrator, without --eraseinstall.
sudo "/Applications/Install macOS <Name>.app/Contents/Resources/startosinstall" \
  --agreetolicense --user "$(id -un)" --passprompt
```

For unattended disposable guests, `--stdinpass` is available in the evaluated full installers. Keep passwords out of persistent files, logs, and host command arguments. Authenticate `sudo` separately before piping the password to `startosinstall`; do not depend on both tools consuming the same input stream.

Launch long preparation as a detached guest process with output redirected to a writable artifact share and an acknowledgement marker. A restart intentionally disconnects GhostTools. Never replay a mutating installer command because the launch acknowledgement or REST connection was lost. Missing completion during reboot is not itself failure, and a successful pre-reboot process exit is not final success.

Keep the helper alive across automatic guest restarts. Poll only read-only guest readiness and exported logs. A bounded maintenance deadline must allow installer preparation, multiple restarts, and cold login; do not apply an ordinary test job's short timeout or stop a progressing installation.

## Session Gates

- Guest exec can work while the console is locked. `ghostvm_guest_ready.sh --require-ghosttools-prompts-clear` requires an unlocked console and checks Setup Assistant as well as browser/account alerts. Run this before XCTest, not only after a failed test.
- A post-upgrade Setup Assistant window or screen lock can leave XCTest's app `Running Background` and prevent activation even with healthy memory and working developer tools.
- Setup Assistant remains a human prerequisite. Do not automate its choices, provision another account, or mark the image unattended-ready because `sw_vers` changed.
- After human completion, preserve the changed current clone disk in a new stopped base. Reverting the earlier candidate discards completed onboarding just as reverting an old OS base discards the upgrade.
- Serialize GUI verification and preparation boots; concurrent maintenance must respect the host's running-VM limit. A failed helper can remain on a modal start-error dialog after removing its PID/socket; clean up only the precisely identified owned helper and restore its guard before retrying.

## Verify And Publish

```bash
python3 scripts/ghostvm_remote_exec.py --name <OwnedClone> --wait-ready 180
python3 scripts/ghostvm_guest_os_check.py \
  --bundle "$HOME/VMs/<OwnedClone>.GhostVM" \
  --expect-version <version> --expect-build <build>
scripts/ghostvm_guest_ready.sh \
  --vm <OwnedClone> --require-python 3.13 \
  --require-ghosttools-prompts-clear --require-xcode-ui-testing
```

- Require the target guest version/build after restart and again on a cold boot. Reject low usable RAM even when the desktop and target OS appear correct.
- Verify retained accounts, Xcode/CLT, the actual selected Python's native imports, the intended GhostTools version, real AppleEvents, and an XCTest interaction. Account or consent dialogs remain failed unattended prerequisites, not successful readiness.
- A required readiness request returning HTTP/guest 408 establishes an inconclusive transport check, not a missing interpreter or disabled developer mode. Retain the failed request, run the equivalent read-only tool checks in a detached artifact-producing job, and record both outcomes. Verify GUI prerequisites before an actual UI test; do not relabel the failed direct readiness command as successful or reinstall tools solely because of its timeout.
- Retain preparation output, `/var/log/install.log`, and relevant guest update logs on failure. A full installer does not necessarily bypass a later boot/firmware failure.
- Remove only task-owned installer payloads, workspaces, and detached jobs. Shut down cleanly from inside the guest; `vmctl stop` signals the helper and is not proof of orderly guest shutdown.
- Restore temporary host configuration/defaults before creating the candidate. Keep original resource/share policy when publishing.
- Create an upgraded base recovery point and prepare from that new base, not the old `clean-state`. Preparation always reverts its `--base-snapshot`, so choosing an old base silently undoes the upgrade.
- If privacy grants need refreshing, seed only while stopped. Select protected macOS 27 stores using their measured `--user-uid`. Legacy home-directory stores use `--user` without `--user-uid`; passing a UID requests a protected store and fails if none exists. Keep Xcode Automation Mode bootstrap separate.
- Back up and replace only the requested prepared snapshot after verification. Cold-boot a fresh clone of the published directory, run successful/nonzero jobs and RO/RW checks, restore its guard, then compare original current-state and unrelated-snapshot fingerprints.

On failure, roll back the complete owned recovery point, including auxiliary storage. Do not transplant auxiliary storage from another disk state or patch fixed firmware offsets as a generic upgrade procedure.

## Whole-VM Consolidation

Use only when the user explicitly authorizes replacing the original current VM, its snapshots, and any saved session. A request to update a prepared snapshot alone does not grant this scope. Honor an explicit no-backups instruction rather than silently retaining old bundles or hidden snapshot copies.

1. Reinventory all original and candidate bundles immediately before selection. Compare current disk/config/auxiliary-storage metadata with the retained checkpoints; inspect any newer current session before reverting it. Onboarding completed after the previous run belongs to the newer disk, not the older `onboarding-required` checkpoint.
2. Select each candidate under its intended runtime and disk format. Verify actual OS/build, usable RAM, selected Python, and retained tools. Shut down inside the guest and capture a new matched stopped checkpoint if the current disk is preferable. Never pair an older checkpoint's auxiliary storage with the newer disk.
3. Cold-boot that selected checkpoint with guarded RO/RW shares. Verify successful and nonzero job exits, configuration restoration, and GUI prerequisites before XCTest. A resumed unlocked session does not prove cold readiness: account reminders can recur after cold boot. Keep transport failures distinct from missing tools.
4. Separate OS/CLI validation from unattended UI certification. Explicitly requested consolidation may retain a known GUI limitation, but must remove stale inventory claims of verified UI readiness. A historical name such as `automation-ready` is not certification; record the actual failed prerequisite and do not schedule unattended UI jobs against it.
5. Stage only the matched `config.json`, `disk.img`, `HardwareModel.bin`, `MachineIdentifier.bin`, and `AuxiliaryStorage.bin`. Require `isSuspended: false`, no saved RAM, no session shares, and the intended resource policy. Compare the destination identity and disk format before replacement. Same-volume `cp -cR` avoids fully copying sparse/APFS-cloned disks.
6. After selection checks pass, replace only the explicitly named canonical bundles. Keep the selected candidate until the canonical destination passes cold OS/memory and CLI checks; this is temporary staging, not retention of the discarded old original. Normal CLI start recreates the path/name-derived `Helper/`; do not carry an obsolete helper app into a renamed bundle.
7. Retain only the requested final recovery points. Remove the obsolete originals, their saved RAM, superseded snapshot histories, task-owned clones, and staging directories. With no-backup scope, do not create a renamed old original or external snapshot backup. Preserve unrelated VMs and measurement evidence.
8. Update the machine-local inventory to canonical paths, actual versions, remaining snapshots, and measured readiness. Verify those bundles through their pinned runtime, stop them, restore guards, and compare unrelated-VM fingerprints. If an unrelated VM is independently used during maintenance, record the observed changes and leave its newer state alone rather than reverting it to satisfy the baseline. No inventory field should continue directing agents to deleted upgrade clones.

Do not overwrite completed onboarding by preparing from an earlier candidate. If further privacy preparation is necessary, first capture the completed current disk as the new base.

## Sources

- Apple Support: [Apple security releases](https://support.apple.com/en-us/100100), [Download and install macOS](https://support.apple.com/en-us/102662), [Volume ownership](https://support.apple.com/guide/deployment/dep02c211f3e/web), [Secure software updates](https://support.apple.com/guide/security/secure-software-updates-secf683e0b36/web).
- Apple Developer Forums: [Guest update reboot failures and reported 26.7.2 success](https://developer.apple.com/forums/thread/848707). Reports describe particular host/guest combinations, not universal compatibility.
- Apple Developer: [Window-server session information](https://developer.apple.com/documentation/coregraphics/cgsessioncopycurrentdictionary()), [Concurrent VM limit errors](https://developer.apple.com/documentation/virtualization/vzerror/code/virtualmachinelimitexceeded).
- GhostVM source contracts: `VMController.installVM`, `VMController.snapshot`, and `VMController.stopVM`; check the selected release before relying on CLI behavior.
- Exact locally measured combinations, outcomes, and remaining limitations belong in `validation.json`, not a claim that every future update will work.
