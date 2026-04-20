---
name: ghostvm-safe-testing
description: Use GhostVM (vmctl + Host API) to run tests in a prepared macOS VM while protecting host data via read-only shared folders and snapshot reverts. Use when the user wants a safe macOS-VM sandbox for testing, mentions GhostVM/GhostTools/vmctl, or needs an agent-friendly closed-loop workflow that must not mutate host inputs.
license: MIT
compatibility: macOS 15+ on Apple Silicon with GhostVM installed, a prepared .GhostVM bundle, GhostTools running in the guest, and a clean snapshot.
metadata:
  author: ghostvm-safe-testing-skill
  version: "0.2.0"
---

# GhostVM safe testing workflow (agent-facing)

## Scope

This skill provides a **repeatable**, **low-risk** workflow to run commands/tests inside a **GhostVM macOS VM**, using:

- **Snapshot revert** to reset VM disk state.
- **Read-only host shared folder** for inputs (repo/data).
- **Writable host shared folder** for outputs (artifacts only).
- **Offline guest-disk seeding** to pre-bake Local Network and baseline TCC state into disposable VM snapshots.

### Use this skill when

- You need to run tests against host data, but must prevent host mutation.
- You want a deterministic “revert → run → export artifacts” loop.
- You need guest automation that should not stop on first-use TCC / Local Network dialogs.
- You are on macOS 15+ (Apple Silicon) and GhostVM is installed.

### Do not use this skill when

- GhostVM is not installed or you are not on Apple Silicon.
- You need fully headless host-side operation with no GUI helper processes.
  - GhostVM’s Host API socket is served by **GhostVMHelper.app** (GUI helper), so `--headless` mode does not provide the Host API.

## Assumptions (prereqs completed by a human)

- GhostVM.app is installed (typically `/Applications/GhostVM.app`) and opened once to clear Gatekeeper quarantine.
- `vmctl` is on `PATH` **via a wrapper script** (not a symlink).
- Target VM bundle exists (default): `~/VMs/<Name>.GhostVM`.
- VM has:
  - completed macOS installation
  - a user login (auto-login recommended)
  - GhostTools installed in the guest and set to auto-launch
- VM has a snapshot named `clean-state` (or you pass another name).

If any of the above is false, run the doctor script; it will print an **ACTION REQUIRED (human)** section.

## Quick start

### 1) Sanity-check host + VM

```bash
scripts/ghostvm_doctor.sh --vm <Name>
```

### 1b) (Optional) Check guest dev readiness

Use this when the VM is a fresh macOS install and you want to avoid CLI workflows that trigger GUI prompts (missing CLT, etc.).

```bash
scripts/ghostvm_guest_ready.sh --vm <Name>
```

See: `references/macos-dev-testing-ready.md`

### 1c) Prepare an unattended automation snapshot (recommended when guest commands use AppleScript/UI automation or local-network discovery)

The recommended path for **disposable VMs** is:

1. Start from a known-good base snapshot.
2. Keep the VM **stopped**.
3. Offline-seed the guest `disk.img` from the host:
   - Local Network CIDR exemptions
   - baseline TCC rows for `/usr/bin/osascript`, `/usr/libexec/sshd-keygen-wrapper`, and GhostTools (`org.ghostvm.com.ghostvm.guest-tools`)
     (including AppleEvents to System Events / Finder / Safari / Mail)
4. Create a new snapshot (for example, `automation-ready`).

```bash
scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready
```

If the snapshot already exists, the helper replaces it (delete + recreate) so re-running the command is safe.

Use priming only when you need extra app-specific approvals beyond the seeded baseline:

```bash
scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --prime-automation \
  --prime-local-network
```

Useful extensions:

```bash
# extra AppleEvents receiver
--appleevent-target com.apple.TextEdit

# extra sender binary whose path should get the same baseline grants
--tcc-client /usr/local/bin/cliclick

# extra app bundle id to grant (Accessibility/ScreenCapture/PostEvent/AppleEvents)
--tcc-bundle-id com.apple.Terminal

# patch only the intended guest user(s)
--user agent
```

Then run the safe loop using the prepared snapshot:

```bash
scripts/ghostvm_safe_test.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --ro /absolute/path/to/host-input \
  --rw /absolute/path/to/host-output \
  --cmd 'swift test'
```

See: `references/headless-automation-gating.md`

### 2) Run a safe test loop

This runner:

- stops the VM (if running)
- reverts the snapshot
- configures shared folders (RO + RW) by editing `config.json`
- starts the VM via GhostVMHelper (in the background)
- waits for the Host API socket + GhostTools `/health`
- copies the RO input into a guest-local workspace
- runs your command inside that workspace
- writes logs + optional `git.diff` into the RW output folder
- optionally stops the VM

```bash
scripts/ghostvm_safe_test.sh \
  --vm <Name> \
  --snapshot clean-state \
  --ro /absolute/path/to/host-input \
  --rw /absolute/path/to/host-output \
  --timeout 3600 \
  --cmd 'swift test'
```

Outputs appear on the host under:

```text
<host-output>/ghostvm-runs/<Name>/<run-id>/
```

### 3) Apply changes back to host (optional)

If the input was a git repo, the runner attempts to export a patch:

```text
.../git.diff
```

Apply it on the host only after review.

## Safety invariants

Keep these true unless the user explicitly opts out:

1. **Host inputs are mounted read-only.**
2. **Work happens on guest-local copies.**
3. **Only a dedicated host output directory is writable.**
4. **VM state is reset via snapshot revert before each run.**
5. **Automation/TCC and Local Network state is baked into disposable snapshots, not granted ad hoc during agent runs.**

## Important behavior notes

### Offline guest-disk seeding is the default prep path for disposable VMs

`ghostvm_prepare_headless_automation.sh` now edits the stopped guest’s `disk.img` from the host. That avoids guest-side SIP/TCC friction for baseline setup and makes the resulting snapshot reproducible.

The helper seeds:

- root-domain Local Network preferences (`com.apple.network.local-network`)
- system and detected per-user `TCC.db` files

If your workflow still prompts after that, the usual cause is that the actual requester binary or AppleEvents receiver differs from the seeded baseline. Add `--tcc-client` / `--appleevent-target`, or use priming for that extra case.

### Snapshots include `config.json`

GhostVM snapshots are coarse-grained copies of bundle files and include `config.json`.

That means `vmctl snapshot ... revert <name>` will overwrite any `config.json` edits (including shared folder settings).

**Therefore:** apply shared-folder edits **after** snapshot revert and before starting the VM.

### `vmctl remote exec` requires an absolute executable path

GhostTools implements `POST /api/v1/exec` by launching `Process(executableURL: command)` in the guest.

That means `vmctl remote exec uname -a` will often fail (because `uname` is not an absolute path), and you may only see:

```text
Error: Invalid response from guest (status 500)
```

Use one of these patterns instead:

```bash
# absolute path
vmctl remote --name <Name> exec /usr/bin/uname -a

# PATH resolution via env
vmctl remote --name <Name> exec /usr/bin/env uname -a

# run a shell pipeline
vmctl remote --name <Name> exec /bin/zsh -lc 'uname -a'
```

### `vmctl remote exec` has a default 30s timeout

GhostTools defaults `POST /api/v1/exec` to a 30s timeout unless the request includes a `timeout` field.

`vmctl remote exec` does not expose that timeout parameter, so it is only suitable for quick checks.

The safe runner uses `scripts/ghostvm_exec.sh` (Host API + timeout) for the long-running guest step.

## Troubleshooting

- If `vmctl` is missing, or `vmctl start` fails with `GhostVMHelper.app not found`: see `references/troubleshooting.md`.
- If the offline prep helper cannot identify the guest data volume: mount the image manually and use `scripts/ghostvm_guest_privacy_seed.py --mounted-root ...`.
- If the Host API socket exists but `/health` fails: ensure GhostTools is installed + running in the guest.
- If `/health` succeeds but `exec` fails: see `references/remote-exec.md`.
