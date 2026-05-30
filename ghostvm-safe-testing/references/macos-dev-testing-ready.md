# Fresh macOS dev/testing readiness checklist (host or VM)

This is a **human-facing** reference for turning a fresh modern macOS install into a reasonably “computer-use / dev-testing ready” environment.

It is written for:

- **macOS Sequoia 15** (and later)
- **macOS Tahoe 26** (and later)
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

Then verify:

```bash
ghostvm-safe-testing/scripts/ghostvm_guest_ready.sh \
  --vm <Name> \
  --require-ghosttools-prompts-clear \
  --require-xcode-ui-testing
```

`--require-ghosttools-prompts-clear` checks the GhostTools setup-window prerequisites that affect UI automation: GhostTools installed in `/Applications`, LaunchAgent/auto-start enabled, GhostTools auto-update enabled, and GhostTools registered as the `http`/`https` default handler. Run GhostTools once interactively to answer the notification permission prompt before snapshotting fresh guests.

### Rosetta 2 (optional)

Rosetta is only needed if you plan to run Intel-only tools/binaries.

**User-driven install:**

- Launch an Intel-only app/binary; macOS prompts to install Rosetta.

**CLI install (managed):**

- `softwareupdate --install-rosetta --agree-to-license`

> Apple has communicated future changes to Rosetta availability in later macOS majors; for long-lived “golden images,” periodically re-check whether you still need Intel-only toolchains.

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
