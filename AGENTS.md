# AGENTS.md

This repo is an **agent skill bundle**. The only production artifact is:

- `ghostvm-safe-testing/SKILL.md`

Everything else exists to support that skill (scripts, assets, references).

## Repo map

- `ghostvm-safe-testing/SKILL.md` — primary entrypoint (keep it concise; progressive disclosure)
- `ghostvm-safe-testing/scripts/` — executable helpers used by agents
  - `ghostvm_doctor.sh` — host + VM sanity checks
  - `ghostvm_guest_ready.sh` — guest dev-ready checks (CLT, optional Rosetta)
  - `ghostvm_safe_test.sh` — safe ‘revert → copy → run → export’ loop
- `ghostvm-safe-testing/assets/` — copy-ready templates for per-project configuration
- `ghostvm-safe-testing/references/` — deeper docs for edge cases / troubleshooting
  - `macos-dev-testing-ready.md` — fresh macOS readiness checklist
- `.agents/skills/update-ghostvm-safe-testing-skill/` — repo-internal skill for keeping this repo in sync with a local GhostVM source checkout

## Working conventions for edits

1. **Keep `SKILL.md` focused**
   - Aim for <500 lines.
   - Put deep troubleshooting and rationale in `references/` and link from `SKILL.md`.

2. **Prefer scripts over brittle prompt instructions** when determinism matters
   - For anything involving VM state transitions, snapshot operations, or config edits, prefer updating scripts.

3. **Be explicit about assumptions**
   - macOS 15+ on Apple Silicon
   - GhostVM installed
   - `vmctl` available on PATH
   - prepared VM with GhostTools running

4. **Error handling policy**
   - Scripts should fail fast with concrete, actionable output.
   - When prerequisites are missing, print an "ACTION REQUIRED (human)" section.

## Local checks

### Static checks (no GhostVM required)

```bash
bash -n ghostvm-safe-testing/scripts/*.sh
python3 -m py_compile ghostvm-safe-testing/scripts/*.py
python3 -m unittest discover -s ghostvm-safe-testing/tests -p 'test_*.py'
```

### Runtime checks (requires GhostVM)

```bash
# sanity check one VM
ghostvm-safe-testing/scripts/ghostvm_doctor.sh --vm <Name>

# dry-run config write
ghostvm-safe-testing/scripts/ghostvm_configure_shares.py \
  --vm <Name> \
  --ro /tmp \
  --rw /tmp \
  --dry-run
```

## Style

- Bash: `set -euo pipefail`, small functions, `--help` support.
- Python: modern stdlib only; keep dependencies at 0.
- Avoid adding new required external tools (`jq`, `gsed`, etc.) unless you also provide a fallback.
