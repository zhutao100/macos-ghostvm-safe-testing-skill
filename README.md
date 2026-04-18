# macOS GhostVM Safe Testing Skill

This repository packages a single **agent skill** for running deterministic, low-risk development tests inside a **macOS VM** managed by **[GhostVM](https://github.com/groundwater/GhostVM)**, while keeping selected host data **read-only**.

It is designed for **LLM agent tools** (Codex CLI, Claude Code, etc.) that need a verifiable loop:

1. Start from a known-clean VM state (snapshot).
2. Mount host inputs read-only (VirtioFS read-only enforcement).
3. Copy inputs into a guest-local workspace.
4. Run commands/tests inside the VM.
5. Export artifacts (logs, patches) to a dedicated host output directory.
6. Stop the VM (optional) and revert to a clean snapshot for the next run.

## Repo contents

- `ghostvm-safe-testing/` — the skill folder
  - `SKILL.md` — the agent-facing entrypoint
  - `scripts/` — ready-to-run helpers
    - `install_vmctl_wrapper.sh` — put a `vmctl` wrapper on your `PATH` (recommended)
    - `ghostvm_doctor.sh` — sanity checks + actionable diagnostics
    - `ghostvm_configure_shares.py` — configure RO/RW shared folders by editing `config.json`
    - `ghostvm_safe_test.sh` — the safe “revert → copy → run → export” loop
  - `references/` — deeper troubleshooting notes

## Install for Codex CLI / agent tools

Codex scans for skills under `~/.agents/skills/**/SKILL.md` (or repo-local `.agents/skills/**/SKILL.md`).

A simple install is to place this repo under your user skills directory:

```bash
mkdir -p ~/.agents/skills
cp -R ./ghostvm-safe-testing-skill ~/.agents/skills/
```

Then restart your agent tool (or reload skills) so it detects:

- `ghostvm-safe-testing/SKILL.md`

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
```

See: `ghostvm-safe-testing/references/macos-dev-testing-ready.md`

## First agent-driven run

From the repo root:

```bash
ghostvm-safe-testing/scripts/ghostvm_doctor.sh --vm <Name>

ghostvm-safe-testing/scripts/ghostvm_safe_test.sh \
  --vm <Name> \
  --snapshot clean-state \
  --ro /Users/me/src/my-repo \
  --rw /Users/me/.ghostvm-artifacts \
  --timeout 3600 \
  --cmd 'swift test'
```

If the doctor or runner reports a missing prerequisite (VM not found, GhostTools unreachable, snapshot missing, `vmctl` wrapper missing), fix it manually and re-run.
