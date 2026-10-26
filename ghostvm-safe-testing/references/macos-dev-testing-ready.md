# Fresh macOS dev/testing readiness checklist (host or VM)

This is a **human-facing** reference for turning a fresh modern macOS install into a reasonably “computer-use / dev-testing ready” environment.

It is written for:

- **macOS Sequoia 15** (and later)
- **macOS Tahoe 26** (and later)
- **macOS 27** guests, including protected per-user consent stores
- Apple silicon hosts/guests

Where possible, prefer baking these steps into a **golden snapshot** (e.g. `clean-state`) so every agent run starts from a known-good baseline.

---

## 1) Baseline OS hygiene

### Apply OS updates

- Bring the machine to the latest patch release.
- Reboot once after major updates.

### Ensure background security updates are enabled

In System Settings → General → Software Update → Automatic Updates:

- Enable the background security/system data updates appropriate for your macOS major version.

(Apple has renamed/tweaked these toggles across 15 → 26; re-check after upgrades.)

Disposable automation images use a different policy: the preparation helper disables update downloads/installs, lock timing, and indexing to keep runs repeatable. Do not apply that policy to the host. See `references/automation-tuning.md`.

---

## 2) Remote access (optional)

If you want to SSH into the machine (useful for headless debugging and for quick provisioning):

- System Settings → General → Sharing → **Remote Login**

Optionally, allow “full disk access for remote users” if you *know* you need it.

---

## 3) Developer tooling

### Xcode Command Line Tools (CLT)

Many developer workflows assume CLT is installed (git/clang/make/xcrun, etc.).

**Interactive install:**

- `xcode-select --install` (opens a GUI prompt)

**Non-interactive / managed install:**

- Use `softwareupdate` to install the “Command Line Tools for Xcode …” update label.
- This typically requires admin privileges and is easiest to bake into your golden snapshot.

> Tip: if CLT is missing, some tools (like `git`) will trigger a GUI prompt, which is hostile to headless agent workflows.

### Full Xcode for macOS UI tests

Xcode/XCTest macOS UI tests need more than CLT. Bake these into a dedicated VM snapshot when the guest will run GUI tests:

- Full Xcode installed under the path you will pass to `--xcode-app` (default: `/Applications/Xcode.app`).
- `xcode-select` points at that Xcode developer directory.
- Xcode license is accepted.
- First-launch setup has completed.
- Developer Tools security is enabled and the automation user belongs to `_developer`.
- XCTest Automation Mode is configured to avoid per-run user authentication.

For disposable GhostVM guests, prefer the skill helper:

```bash
ghostvm-safe-testing/scripts/ghostvm_prepare_xcode_ui_testing.sh \
  --vm <Name> \
  --base-snapshot clean-state \
  --user agent
```

If the disposable guest has a known sudo password and no passwordless sudo policy, pass it through `GHOSTVM_GUEST_SUDO_PASSWORD` for that invocation only. Do not persist guest passwords in repo files.

For macOS 27 selected accounts, also pass `--user-uid <uid>` using `id -u` from inside the guest. Read `references/runtime-compatibility.md` before preparing protected TCC stores or switching v2/v3 runtimes.

Then verify:

```bash
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

`--require-ghosttools-prompts-clear` checks GhostTools installation, LaunchAgent/auto-start, auto-update, native `http`/`https` application handlers, CoreServicesUIAgent and UserNotificationCenter windows, and a running Setup Assistant. Hidden onboarding can have no windows while still blocking normal LaunchAgents. Default-browser registration can enqueue multiple dialogs even when both handler lookups already report GhostTools; identity-regenerated clones can also surface guest account alerts. Clear them inside the owned guest, then verify a real XCTest launch and interaction before snapshotting. Run GhostTools once to answer the notification permission prompt on fresh guests.

### Rosetta 2 (optional)

Rosetta is only needed if you plan to run Intel-only tools/binaries.

**User-driven install:**

- Launch an Intel-only app/binary; macOS prompts to install Rosetta.

**CLI install (managed):**

- `softwareupdate --install-rosetta --agree-to-license`

> Apple has communicated future changes to Rosetta availability in later macOS majors; for long-lived “golden images,” periodically re-check whether you still need Intel-only toolchains.

### Guest Python

Provision Python only when the intended workload requires it. Check the actual job interpreter, not an inventory capability label or a successful `python3 --version`:

```bash
scripts/ghostvm_guest_ready.sh --vm <Disposable> --require-python 3.13
# Or select a known versioned executable explicitly.
scripts/ghostvm_guest_ready.sh --vm <Disposable> --require-python 3.13 \
  --python /opt/homebrew/bin/python3.14
```

- Xcode's `/usr/bin/python3` can resolve to Python 3.9 even when Homebrew Python 3.14 is installed.
- Homebrew's versioned formula puts generic `python3` links in `$(brew --prefix python@3.14)/libexec/bin`. Add that directory to the automation user's login PATH when generic commands should select it; check both `/bin/zsh -lc` and `/bin/bash -lc`. Keep explicit executable paths for non-shell Host API calls.
- Native stdlib extensions must load too. In the evaluated macOS 26.2 snapshot, Python 3.14.4 printed its version but `plistlib` failed because `pyexpat` required an unavailable system expat symbol. Homebrew documents that these Tahoe bottles require macOS 26.3 or later: [upstream explanation](https://github.com/Homebrew/homebrew-core/issues/277330). Use a supported OS patch or a compatible interpreter distribution; do not make global `DYLD_LIBRARY_PATH` changes or patch packaged binaries merely to hide a baseline mismatch.
- For a Python.org framework installation, complete its bundled `Install Certificates.command` setup before HTTPS workloads. Verify a nonempty `ssl.create_default_context().cert_store_stats()` and a bounded HTTPS request. The skill's Python gate checks version, imports, and XML/SQLite operations, not network trust. Keep the selected distribution explicit and unlink conflicting CLI entry points rather than overwriting another package manager's files.
- Repair a clone and verify a cold boot before publishing the prepared snapshot. Record the OS build, interpreter path/version, and functional checks. Do not install into the host or a user's suspended current disk.

### Automation User Profile

Deferring a recurring account reminder does not establish a durable repair; a real XCTest can pass while an unrelated system alert still remains. Report native test results separately from unattended prompt readiness.

- Preserve the original snapshot and current/suspended session. Do not revoke cloud credentials from a clone that shares them with the original.
- Keep the existing local account, UID, profile, and hardware identity for scoped tooling repairs. Creating another account or replacing its home is not a routine prompt fix.
- A replaced profile can run MiniBuddy and block normal LaunchAgents despite completed machine setup and auto-login. Copied preferences or zero visible windows do not prove onboarding is complete.
- Verify GhostTools startup, native browser handlers, notification consent, AppleEvents, and an XCTest interaction after a fresh cold boot. Keep prompt detection enabled; do not suppress it to make readiness pass.
- If a personal-account reminder persists, retain the diagnostic as a manual prerequisite rather than broadening the repair into account provisioning.
- If a secondary guest transport is enabled temporarily for recovery, retain its original configuration and restore it before publishing. Do not modify host SSH, helper selection, or global Xcode settings.

---

## 4) Keep the machine awake during long runs (optional)

For long running builds/tests where sleep breaks workflows:

- Use `caffeinate` while the run is active.

This is especially relevant for “headless” machines or VMs that are left unattended.

---

## 5) GhostVM-specific guest setup (VM only)

These are the high-friction parts that are usually not fully automatable:

- Complete Setup Assistant
- Create an admin user
- Enable auto-login (recommended for agent workflows)
- Install GhostTools and add it to Login Items

Once the guest is stable:

- Create a snapshot (e.g. `clean-state`) and treat it as the golden baseline.

---

## 6) Headless automation gating (TCC + Xcode UI testing + Local Network)

If you intend to run unattended automation inside the guest (AppleScript/UI automation, Xcode/XCTest UI tests, local device discovery, etc.), bake the required consent state into a snapshot.

See: `references/headless-automation-gating.md`
