# GhostVM behavior notes (used by this skill)

## Shared folders and mount points

GhostVM builds a **single VirtioFS device** that can expose **multiple** host directories to the guest.

Inside the guest:

- The shared folders are exposed via a single **AppleVirtIOFS** volume (VirtioFS), typically mounted under:

```text
/Volumes/<virtiofs-volume>
```

- Each configured shared folder appears as a directory under that volume:

```text
/Volumes/<virtiofs-volume>/<share-name>
```

- `<share-name>` is the **leaf directory name** of the host path.
  - Example: host `/Users/me/src/my-repo` → guest `/Volumes/My Shared Files/my-repo`

You can discover the mountpoint by running in the guest:

```bash
/sbin/mount | /usr/bin/grep -m 1 AppleVirtIOFS
```

If two shares have the same leaf name, GhostVM disambiguates by appending `-2`, `-3`, etc.

## How this skill configures multiple shares

GhostVM persists VM settings in:

```text
<MyVM>.GhostVM/config.json
```

The key used for multiple shares is:

```json
"sharedFolders": [
  {"id":"<uuid>","path":"/host/path","readOnly":true},
  {"id":"<uuid>","path":"/host/out","readOnly":false}
]
```

This skill edits `config.json` while the VM is stopped to set:

> **Important:** GhostVM snapshots are *coarse-grained copies of bundle files* and include `config.json`.
> That means `vmctl snapshot ... revert` will overwrite any `config.json` edits.
>
> **Therefore:** always apply shared-folder edits **after** snapshot revert and before starting the VM.

- exactly one RO folder (the input)
- exactly one RW folder (the artifact output)

## Why scripts do not use `vmctl remote exec` for long runs

The GhostTools `/api/v1/exec` endpoint supports a `timeout` field (seconds).

`vmctl remote exec` does not currently expose that timeout field; it relies on the default.

For test commands that can exceed the default, this skill calls the Host API directly via:

```bash
curl --unix-socket ~/Library/Application\ Support/GhostVM/api/<Name>.GhostVM.sock \
  -X POST -H 'Content-Type: application/json' \
  -d '{"command":"/bin/bash","args":["-lc","..."],"timeout":7200}' \
  http://localhost/api/v1/exec
```

`scripts/ghostvm_exec.sh` wraps this pattern.

## Why scripts background `vmctl start`

`vmctl start <bundle>` launches a per-VM helper app via `open -W`, which waits until the helper exits.

To keep automation non-blocking, `scripts/ghostvm_safe_test.sh` starts/resumes the VM in the background, then waits for:

- the Host API socket to appear
- `/health` to return ok

## Export strategy

To keep host mutation risk low:

1. Mount host input read-only.
2. Copy input into a guest-local workspace.
3. Run the test command in the guest-local workspace.
4. Write artifacts into the RW share (host output directory).

If the input is a git repo, the runner also attempts:

- `git diff` → `git.diff`
