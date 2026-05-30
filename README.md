# macOS GhostVM Safe Testing Skill

This repository packages a single **agent skill** for running deterministic, low-risk development tests inside a **macOS VM** managed by **[GhostVM](https://github.com/groundwater/GhostVM)**, while keeping selected host data **read-only**.

It is designed for **LLM agent tools** (Codex CLI, Claude Code, etc.) that need a verifiable loop:

1. Start from a known-clean VM state (snapshot).
2. Mount host inputs read-only, with an in-guest write probe before running commands.
3. Copy inputs into a guest-local workspace.
4. Run commands/tests inside the VM.
5. Export artifacts (logs, patches) to a dedicated host output directory.
6. Stop the VM (optional) and revert to a clean snapshot for the next run.

It also includes a **disposable-VM preparation path** for guest automation that would otherwise block on **TCC**, **Xcode/XCTest Automation Mode**, Developer Tools, or **Local Network** first-use prompts.

## Repo contents

- `ghostvm-safe-testing/` — the skill folder
  - `SKILL.md` — the agent-facing entrypoint
  - `config/` — skill-local configuration files
    - `local-vms.example.json` — tracked template for a machine-local VM inventory
    - `local-vms.json` — git-ignored local VM inventory for agents to consult before asking for paths/snapshots
  - `scripts/` — ready-to-run helpers
    - `install_vmctl_wrapper.sh` — put a `vmctl` wrapper on your `PATH` (recommended)
    - `ghostvm_doctor.sh` — sanity checks + actionable diagnostics
    - `ghostvm_configure_shares.py` — configure RO/RW shared folders by editing `config.json`
    - `ghostvm_guest_privacy_seed.py` — offline guest-disk seeding for Local Network + baseline TCC, including Xcode UI-testing candidates
    - `ghostvm_guest_tune_automation.py` — offline vanilla macOS tuning for disposable automation snapshots
    - `ghostvm_automation_guard.py` — temporary GhostVM config/helper-default guard with restore state
    - `ghostvm_guest_bootstrap_xcode_ui_testing.sh` — in-guest root bootstrap for Xcode UI tests (Automation Mode, Developer Tools, first launch)
    - `ghostvm_prepare_headless_automation.sh` — build an `automation-ready` or `xcode-ui-ready` snapshot from a stopped guest image
    - `ghostvm_prepare_xcode_ui_testing.sh` — convenience wrapper for standard Xcode/XCTest UI-testing snapshot prep
    - `ghostvm_safe_test.sh` — the safe “revert → copy → run → export” loop
  - `references/` — deeper troubleshooting notes

## Install for Codex CLI / agent tools

Codex scans for skills under `~/.agents/skills/**/SKILL.md` (or repo-local `.agents/skills/**/SKILL.md`).

A simple install is to place the skill folder under your user skills directory:

```bash
mkdir -p ~/.agents/skills
cp -R ./ghostvm-safe-testing ~/.agents/skills/
```

Then restart your agent tool (or reload skills) so it detects:

- `~/.agents/skills/ghostvm-safe-testing/SKILL.md`

## Local VM inventory

For repeat agent sessions, create a machine-local inventory:

```bash
cp ghostvm-safe-testing/config/local-vms.example.json ghostvm-safe-testing/config/local-vms.json
```

Keep `ghostvm-safe-testing/config/local-vms.json` updated with VM bundle paths, prepared snapshot names, guest accounts, and tool paths. The file is git ignored and lives under the installed skill directory so agents can consult the same local disposable-guest details after installing only `ghostvm-safe-testing/`.

## Human prerequisites (cannot be fully automated)

Agents can drive `vmctl` and the Host API, but **cannot** reliably complete macOS GUI/security setup steps. Do these once per host machine.

### 1) Install GhostVM.app and clear Gatekeeper quarantine

1. Download GhostVM from the upstream releases.
2. Drag `GhostVM.app` to `/Applications`.
3. Launch it once (or right-click → Open) so macOS prompts are acknowledged.

### 2) Ensure `vmctl` is runnable from your shell

GhostVM bundles `vmctl` at:

```text
/Applications/GhostVM.app/Contents/PlugIns/Helpers/vmctl.app/Contents/MacOS/vmctl
```

You want a short `vmctl` on `PATH`, but **do not symlink** the binary.

If `vmctl` is invoked as just `vmctl` (argv0 is not an absolute path), GhostVM currently may fail to find `GhostVMHelper.app` and you will see:

```text
Error: GhostVMHelper.app not found. Use --headless or run vmctl from within GhostVM.app.
```

Use the wrapper installer instead:

```bash
# installs a small wrapper script named `vmctl` into a PATH directory
# (default: /usr/local/bin if writable, else ~/.local/bin)
./ghostvm-safe-testing/scripts/install_vmctl_wrapper.sh --ghostvm-app /Applications/GhostVM.app

# verify
command -v vmctl
vmctl --help
```

> Note: if you install to `/usr/local/bin`, you may need to run with `sudo` depending on your machine.

### 3) Create and provision a VM for agent use

You must have at least one installed `.GhostVM` bundle (default location):

- `~/VMs/<Name>.GhostVM`

Required steps:

1. Create VM from a restore image (IPSW) via GhostVM GUI.
2. Install macOS and complete Setup Assistant.
3. Create a dedicated admin user.
4. **Enable auto-login** for that user (recommended for agent workflows).
5. Install **GhostTools** inside the guest VM:
   - in `GhostVM.app`: `Contents/Resources/GhostTools.dmg`
   - copy into the guest and install `GhostTools.app` to `/Applications`
   - add GhostTools to **Login Items** so it runs after login.

### 4) Create a clean snapshot

Pick a stable snapshot name (default used by scripts: `clean-state`).

```bash
vmctl snapshot ~/VMs/<Name>.GhostVM create clean-state
```

### 5) Decide your host input/output directories

For safe testing, you typically need **two** host paths:

- **Read-only input** (repo, dataset, fixtures)
- **Writable output** (artifacts directory dedicated to VM runs)

Recommendations:

- Use **different leaf directory names** (GhostVM shares are keyed by leaf name).
- Keep the writable output path **separate** from any production-like data.

Example:

```text
/Users/me/src/my-repo            (RO)
/Users/me/.ghostvm-artifacts     (RW)
```

## Optional: verify a fresh guest is dev-ready

If your VM guest is a fresh macOS install, run:

```bash
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh --vm <Name>

# for snapshots intended to run Xcode/XCTest macOS UI tests
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

See: `ghostvm-safe-testing/references/macos-dev-testing-ready.md`

## Optional: prepare a disposable automation snapshot (recommended for AppleScript/UI automation, Xcode UI tests, or local-network workflows)

The pragmatic path for disposable VMs is:

1. Revert to a known-good base snapshot.
2. Keep the VM stopped.
3. Offline-seed the guest `disk.img` from the host:
   - Local Network CIDR exemptions
   - baseline TCC rows for `/usr/bin/osascript`, `/usr/libexec/sshd-keygen-wrapper`, GhostTools, and optional Xcode UI-testing clients/services
   - Safari's **Allow JavaScript from Apple Events** preference for detected guest users
   - vanilla macOS automation tuning: automatic update downloads/installs off, timed lock suppressed, Time Machine prompts suppressed, Spotlight indexing opt-out marker
   - host-side GhostVM config guard for prompt-prone shared-folder, networking, port-forward, and helper-default settings
4. When `--xcode-ui-testing` is used, boot once and run a guest-side bootstrap for `automationmodetool`, Developer Tools, and Xcode first-launch setup.
5. Create a new snapshot, for example `automation-ready` or `xcode-ui-ready`.

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready
```

If the snapshot already exists, the helper fails before changing VM state. Choose a new prepared snapshot name, or add `--replace-snapshot` only when you explicitly intend to delete and recreate that snapshot.

For Xcode/XCTest macOS UI tests, prepare a dedicated snapshot:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot xcode-ui-ready \
  --xcode-ui-testing \
  --user agent
```

Equivalent wrapper:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --user agent
```

The Xcode UI-testing path needs administrator privileges inside the disposable guest. The helper uses noninteractive sudo. Prefer temporary passwordless sudo for golden-image prep; for disposable guests with a known password, pass it through the host environment for that run only:

```bash
GHOSTVM_GUEST_SUDO_PASSWORD='<guest-password>' \
  ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
    --vm <Name> \
    --base-snapshot clean-state \
    --user agent
```

If noninteractive sudo is unavailable, run `ghostvm_guest_bootstrap_xcode_ui_testing.sh` once inside the guest with `sudo`, then snapshot the prepared VM. Do not persist guest passwords in repo files.

Useful extensions:

```bash
# extra AppleEvents receiver
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --appleevent-target com.apple.TextEdit

# extra sender binary that should receive the same baseline grants
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --tcc-client /usr/local/bin/cliclick

# include Xcode.app / Xcode Helper.app / xcodebuild / xcrun candidates and Automation Mode setup
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot xcode-ui-ready \
  --xcode-ui-testing \
  --xcode-app /Applications/Xcode.app

# add an extra TCC service when attribution shows a new gate
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --tcc-service kTCCServiceListenEvent

# leave Safari's JavaScript-from-Apple-Events setting unchanged
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --skip-safari-js-apple-events

# explicitly delete and recreate an existing target snapshot
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --snapshot automation-ready \
  --replace-snapshot
```

Use interactive priming only for approvals that are intentionally outside the seeded baseline:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_headless_automation.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --prime-automation \
  --prime-local-network
```

See: `ghostvm-safe-testing/references/headless-automation-gating.md`

## First agent-driven run

From the repo root:

```bash
ghostvm-safe-testing/scripts/ghostvm_doctor.sh --vm <Name>

ghostvm-safe-testing/scripts/ghostvm_safe_test.sh \
  --vm <Name> \
  --snapshot automation-ready \
  --ro /Users/me/src/my-repo \
  --rw /Users/me/.ghostvm-artifacts \
  --timeout 3600 \
  --keep-running \
  --cmd 'swift test'
```

The runner copies the read-only input into a guest-local workspace before running
the command. It exports runner logs, exit code, environment notes, and `git.diff`
to the writable host output path. Command-generated artifacts under the copied
project tree stay guest-local unless the command copies them to the RW share. For
Xcode UI runs, include an explicit copy/`ditto` step for `.artifacts/ui` or the
latest `.xcresult` into `/Volumes/My Shared Files/<rw-leaf>/...`.

For repeated guest work, pass `--keep-running` to `ghostvm_safe_test.sh` when follow-up commands or inspection are likely. This avoids repeated VM bring-up and shutdown churn. The runner prints an `automation_state.json` path; when done, stop and restore with:

```bash
python3 ghostvm-safe-testing/scripts/ghostvm_automation_guard.py restore \
  --state /path/to/ghostvm-runs/<Name>/<run-id>/automation_state.json \
  --stop-vm
```

Shared folders are persistent VM settings. The safe runner restores its pre-run config when it finishes; if manual setup adds session-scoped shares, especially temp or ephemeral host directories, remove those shares or restore the previous settings before wrapping up.

If the doctor or runner reports a missing prerequisite (VM not found, GhostTools unreachable, snapshot missing, `vmctl` wrapper missing), fix it manually and re-run.
