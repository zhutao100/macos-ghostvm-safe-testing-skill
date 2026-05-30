---
name: ghostvm-safe-testing
description: Use GhostVM/vmctl to run macOS tests in a disposable VM with snapshot reverts, read-only host inputs, writable artifact export, automation prompt pre-seeding, and host config restoration. Trigger when a task mentions GhostVM, GhostTools, vmctl, macOS VM sandboxing, or safe agentic VM testing.
license: MIT
compatibility: macOS 15+ / macOS 26+ on Apple Silicon with GhostVM.app installed, a prepared .GhostVM bundle, GhostTools running in the guest, and Python 3 on the host.
metadata:
  author: ghostvm-safe-testing-skill
  version: "0.4.0"
---

# GhostVM safe testing

## Execution contract

Use this skill to run macOS commands/tests inside a GhostVM guest without mutating host inputs.

Default invariants:

1. Revert a known snapshot before the run.
2. Mount host input read-only and host artifact output writable.
3. Copy input into a guest-local workspace before running commands.
4. Export only logs, patches, and requested artifacts to the writable output.
5. Prefer `--keep-running` for any likely multi-round agent workflow.
6. Before final reporting, stop the VM and restore temporary GhostVM config/defaults.
7. Do not delete, replace, or recreate existing snapshots unless the user explicitly requested it.

## First checks

1. Check `config/local-vms.json` if present before asking for VM names, bundle paths, snapshots, or disposable guest users.
2. Run the doctor before assuming the VM is usable:

```bash
scripts/ghostvm_doctor.sh --vm <Name> --snapshot clean-state
```

Use `--no-start` only when you need a static check.

If `vmctl` is missing or is a symlink, install the wrapper:

```bash
scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app
```

GhostVM Host API requires normal helper start. Do not use `vmctl start --headless` for this skill’s remote-exec workflow.

## Prepare an automation-ready snapshot

Use this when the VM is fresh, when UI/AppleScript/local-network prompts are expected, or before long-running agent sessions.

```bash
scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready
```

This performs stopped-VM offline seeding and tuning:

- Local Network CIDR exemptions.
- TCC grants for baseline automation clients and optional Xcode UI-testing clients.
- Safari JavaScript-from-Apple-Events preference for selected/detected users.
- Vanilla macOS automation tuning: automatic update downloads/installs off, timed screen lock suppressed, Time Machine new-disk prompts suppressed, Spotlight indexing opt-out marker.
- Host config guard around prepared snapshot creation and any priming boot, so stale shares, bridged networking, port forwards, and helper defaults do not block automation.

Rules:

- If `--snapshot` already exists, choose a new snapshot name.
- Use `--replace-snapshot` only after explicit user instruction to overwrite that snapshot.
- Use `--keep-security-responses` only when the user wants Security Responses/system data auto-install to remain unchanged.

For Xcode/XCTest UI automation:

```bash
scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --user agent
```

Then verify guest UI prompt readiness:

```bash
scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

Read deeper prep details only when needed:

- `references/headless-automation-gating.md`
- `references/macos-dev-testing-ready.md`
- `references/automation-tuning.md`

## Run the safe loop

Use `--keep-running` by default when follow-up guest commands, log inspection, or iterative fixes are likely.

```bash
scripts/ghostvm_safe_test.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --ro /absolute/path/to/host-input \
  --rw /absolute/path/to/host-output \
  --timeout 3600 \
  --keep-running \
  --cmd 'swift test'
```

The runner:

1. Stops any running VM.
2. Reverts the snapshot.
3. Applies temporary automation guards:
   - switches bridged networking to NAT for the run,
   - clears port forwards,
   - clears stale shared-folder settings before the run-specific RO/RW shares are written,
   - disables GhostVM helper auto-port-map, clipboard-sync, and URL auto-open defaults for this VM.
4. Configures RO/RW shared folders and clears stale legacy shared-folder paths.
5. Starts the VM through GhostVMHelper.
6. Waits for Host API socket, GhostTools `/health`, remote exec readiness, and VirtioFS mounts.
7. Verifies RO is not writable and RW is writable from inside the guest.
8. Runs the command in `/Users/Shared/ghostvm-safe-testing/<run-id>/input`.
9. Exports logs, `exit_code`, `sw_vers.txt`, `uname.txt`, and optional git patch files to:

```text
<host-output>/ghostvm-runs/<Name>/<run-id>/
```

Snapshots include `config.json`, so the runner always reverts before saving guard state and editing shared folders.

## Continue a kept-running VM

The safe runner prints the Host API socket path and the restore command. For additional commands, use:

```bash
scripts/ghostvm_exec.sh --socket <socket-path> --timeout 600 --argv /bin/zsh -lc '<command>'
```

For raw Host API details, load `references/remote-exec.md`.

## Finish a multi-round session

When `--keep-running` was used, stop the VM and restore the pre-run GhostVM config/defaults before reporting completion:

```bash
python3 scripts/ghostvm_automation_guard.py restore \
  --state /path/to/host-output/ghostvm-runs/<Name>/<run-id>/automation_state.json \
  --stop-vm
```

If `--keep-running` was not used, `ghostvm_safe_test.sh` restores automatically after stopping the VM.

## Snapshot policy

Never run these unless the user explicitly asked to replace/delete an existing snapshot:

```bash
vmctl snapshot <bundle> delete <snapshot>
scripts/ghostvm_prepare_headless_automation.sh ... --replace-snapshot
```

When a target snapshot exists, pick a new name such as `automation-ready-YYYYMMDD` and tell the user which one was created.

## Failure handling

Use `references/troubleshooting.md` when these occur:

- Host API socket never appears.
- VM was started with `--headless` and has no Host API.
- GhostTools `/health` fails.
- Shared folders do not mount or expected leaf names differ.
- A GhostVM helper alert blocks automation.

Before starting a VM manually, preflight config prompt blockers:

```bash
python3 scripts/ghostvm_automation_guard.py inspect --bundle /path/to/<Name>.GhostVM
```

## Development checks for this skill repo

```bash
bash -n ghostvm-safe-testing/scripts/*.sh
python3 -m py_compile ghostvm-safe-testing/scripts/*.py
python3 -m unittest discover -s ghostvm-safe-testing/tests -p 'test_*.py'
```
