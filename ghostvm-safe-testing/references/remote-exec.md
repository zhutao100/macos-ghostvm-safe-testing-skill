# Remote exec troubleshooting (`vmctl remote ... exec`)

## Symptom

- `vmctl remote --name <Name> health` works.
- `vmctl remote --name <Name> apps` works.
- `vmctl remote --name <Name> clipboard get/set` works.
- But **any** `exec` call returns immediately with:

```text
Error: Invalid response from guest (status 500)
```

The same happens with:

- Host API `POST /api/v1/exec`
- Unix socket + curl equivalents

## Root cause (as implemented today)

GhostTools implements `POST /api/v1/exec` by launching a macOS `Process` with:

- `executableURL = file://<command>`
- `arguments = <args>`

This means **`command` must be a valid executable path** in the guest. It is **not** searched in `$PATH`.

So these will fail:

```bash
vmctl remote --name <Name> exec uname -a
vmctl remote --name <Name> exec ls -la
```

Because `uname` / `ls` are not absolute paths.

## Fix patterns

### 1) Use absolute paths

```bash
vmctl remote --name <Name> exec /usr/bin/uname -a
vmctl remote --name <Name> exec /bin/ls -la /
```

### 2) Use `/usr/bin/env` for PATH resolution

```bash
vmctl remote --name <Name> exec /usr/bin/env uname -a
vmctl remote --name <Name> exec /usr/bin/env swift --version
```

### 3) Use a shell for pipelines / compound commands

```bash
vmctl remote --name <Name> exec /bin/zsh -lc 'uname -a'
vmctl remote --name <Name> exec /bin/zsh -lc 'cd /tmp && ls -la'
```

This is the most flexible option and is what the safe runner uses.

## If absolute paths still fail

At that point the failure is not the PATH issue. Common next steps:

1. Confirm GhostTools is running as the logged-in user.
   - If auto-login is disabled and no user session is active, GhostTools won’t launch.
2. In the guest, open **Console** and filter for `GhostTools`.
   - Or run:

   ```bash
   log stream --level debug --predicate 'process == "GhostTools"'
   ```

3. Reinstall GhostTools (copy `GhostTools.dmg` again and replace the app).
4. Reboot the guest VM and retry.
