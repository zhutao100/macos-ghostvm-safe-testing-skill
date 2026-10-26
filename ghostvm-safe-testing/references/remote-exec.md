# Remote exec troubleshooting (`vmctl remote ... exec`)

## Transport Limits

Read [Runtime Compatibility](runtime-compatibility.md#transport-checks) before diagnosing v3 failures: both releases can block on large REST output, and v3 can fail after 30 silent seconds despite a longer command deadline. Use `ghostvm_safe_test.sh` for long jobs; it launches briefly and polls an atomic completion artifact. `/health` is host liveness, not successful guest execution.

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

### 0) Right after boot: transient `Connection reset by peer`

Immediately after the VM boots (even if `/health` is already OK), `exec` can transiently fail with:

```text
Error: Error Domain=NSPOSIXErrorDomain Code=54 "Connection reset by peer"
```

Doctor, preparation, and safe runs retry a fixed read-only guest probe for up to 120 seconds by default. Cold login and GhostTools startup can exceed 30 seconds. Override the readiness budget with `GHOSTVM_READY_TIMEOUT=<seconds>`; this is separate from the guest job's `--timeout`.

For a running owned clone:

```bash
python3 scripts/ghostvm_remote_exec.py --socket <socket> --wait-ready 120
```

Each probe is bounded by the remaining deadline, with a maximum 10-second host request. Readiness requires both `exitCode: 0` and the expected probe output; an empty successful response is insufficient. This mode never retries a user-supplied command. Ordinary `ghostvm_remote_exec.py` requests have a 30-second host deadline, adjustable with `--timeout`; expiry does not prove the guest process stopped. Do not replay mutating commands after an uncertain transport result.

Required guest readiness checks (Python, CLT, Xcode/Automation Mode, and the compound prompt scan) use `ghostvm_exec.sh --timeout 120` to send a 120-second guest deadline, with a bounded 135-second host request. Cold Xcode/LaunchServices/System Events queries can exceed 30 seconds. Increasing only the Python wrapper's host deadline cannot change the guest's default 30-second limit. Neither deadline extends the v3 guest transport's fixed idle limit.

These checks require positive tool output as well as success and retain API/timeout diagnostics. The CLI can synthesize an empty zero-status result from a response without command fields; it is not capability evidence. A failed request is an unverified prerequisite, not proof that installed tools are absent.

The safe runner publishes an atomic `job_started` marker before copying input or running the workload. If the short launch request fails, it waits up to 30 seconds (bounded by the job deadline) for that marker or `exit_code`; it never resends the launch. A confirmed job continues to its real completion status. Without either artifact, it stops the VM and restores the guard even with `--keep-running`, rather than leaving an unconfirmed job active.

1. Confirm GhostTools is running as the logged-in user.
   - If auto-login is disabled and no user session is active, GhostTools won’t launch.
2. In the guest, open **Console** and filter for `GhostTools`.
   - Or run:

   ```bash
   log stream --level debug --predicate 'process == "GhostTools"'
   ```

3. Reinstall GhostTools (copy `GhostTools.dmg` again and replace the app).
4. Reboot the guest VM and retry.
