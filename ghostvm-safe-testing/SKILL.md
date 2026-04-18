---
name: ghostvm-safe-testing
description: Use GhostVM (vmctl + Host API) to run tests in a prepared macOS VM while protecting host data via read-only shared folders and snapshot reverts. Use when the user wants a safe macOS-VM sandbox for testing, mentions GhostVM/GhostTools/vmctl, or needs an agent-friendly closed-loop workflow that must not mutate host inputs.
license: MIT
compatibility: macOS 15+ on Apple Silicon with GhostVM installed, a prepared .GhostVM bundle, GhostTools running in the guest, and a clean snapshot.
metadata:
  author: ghostvm-safe-testing-skill
  version: "0.1.0"
---

# GhostVM safe testing workflow (agent-facing)

## Scope

This skill provides a **repeatable**, **low-risk** workflow to run commands/tests inside a **GhostVM macOS VM**, using:

- **Snapshot revert** to reset VM disk state.
- **Read-only host shared folder** for inputs (repo/data).
- **Writable host shared folder** for outputs (artifacts only).

### Use this skill when

- You need to run tests against host data, but must prevent host mutation.
- You want a deterministic “revert → run → export artifacts” loop.
- You are on macOS 15+ (Apple Silicon) and GhostVM is installed.

### Do not use this skill when

- GhostVM is not installed or you are not on Apple Silicon.
- You need fully headless automation with no GUI processes.
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

## Important behavior notes

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
- If the Host API socket exists but `/health` fails: ensure GhostTools is installed + running in the guest.
- If `/health` succeeds but `exec` fails: see `references/remote-exec.md`.
