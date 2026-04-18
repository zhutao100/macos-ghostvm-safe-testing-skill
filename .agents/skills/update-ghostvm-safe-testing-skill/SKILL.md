---
name: update-ghostvm-safe-testing-skill
description: Keep this repo's ghostvm-safe-testing skill accurate and up to date by validating against a local GhostVM source checkout (docs + Swift source) and then updating scripts/docs/tests to match current GhostVM behavior.
compatibility: Requires a local checkout of groundwater/GhostVM on the same machine and basic CLI tools (bash, python3).
metadata:
  version: "0.1.0"
---

# Update workflow for this repo (repo-internal skill)

## Purpose

Use this skill **inside this repository** when you need to:

- incorporate upstream GhostVM behavior changes into the `ghostvm-safe-testing` skill
- validate that scripts and docs still match GhostVM’s actual implementation
- keep the repo compliant with Codex CLI skill loading rules and the Agent Skills spec

This skill assumes you have a **local clone** of the upstream GhostVM repo available.

## Setup

1) Copy the config template and set your local GhostVM checkout path:

```bash
cd .agents/skills/update-ghostvm-safe-testing-skill
cp assets/ghostvm_source_repo.config.json.example assets/ghostvm_source_repo.config.json
$EDITOR assets/ghostvm_source_repo.config.json
```

The config is a small JSON file:

```json
{
  "ghostvm_repo_path": "~/src/GhostVM"
}
```

## Fast validation

Run the verifier to check a small set of **contract assumptions** (socket paths, snapshot contents, exec semantics, etc.):

```bash
.agents/skills/update-ghostvm-safe-testing-skill/scripts/verify_against_ghostvm_repo.py
```

If it reports failures, use the details to drive targeted edits.

## Full update procedure

1) **Inspect upstream GhostVM changes**

- Read `GhostVM/docs/` for user-facing behavior changes.
- Cross-check in source where behavior matters for automation:
  - `macOS/GhostVM/vmctl/*` (CLI behavior)
  - `macOS/GhostVMHelper/*` (Host API socket lifecycle)
  - `macOS/GhostTools/*` (guest-side API semantics)
  - `macOS/GhostVMKit/*` (bundle format, snapshots, shared folders)

2) **Update this skill** (`ghostvm-safe-testing/`)

- Keep `SKILL.md` concise; push deep rationale into `references/`.
- Update scripts first when behavior is operational (ordering, timeouts, socket discovery, etc.).
- Prefer *source code truth* over docs when they disagree.

3) **Run local checks** (no GhostVM required)

From repo root:

```bash
bash -n ghostvm-safe-testing/scripts/*.sh
python3 -m py_compile ghostvm-safe-testing/scripts/*.py
python3 -m unittest discover -s ghostvm-safe-testing/tests -p 'test_*.py'
```

4) **Re-run the verifier**

```bash
.agents/skills/update-ghostvm-safe-testing-skill/scripts/verify_against_ghostvm_repo.py
```

5) **Maintain spec compliance**

- Ensure each skill directory name matches `SKILL.md` frontmatter `name`.
- Ensure `SKILL.md` frontmatter includes `name` and `description`.
- Keep optional assets/scripts in the standard subfolders.

## What this skill should not do

- Do not attempt to automate Gatekeeper quarantine prompts, Setup Assistant, or enabling auto-login.
- Do not “fix” GhostVM behavior by editing the user’s GhostVM checkout.
- Avoid adding new non-stdlib dependencies.
