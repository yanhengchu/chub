---
name: chrome-cdp
description: Manage a persistent local Chrome instance through the Chrome DevTools Protocol on macOS, Ubuntu, and Windows. Use when Codex needs to start, stop, inspect, or reuse a CDP-enabled Chrome; prepare an isolated debug profile; or connect Playwright to the shared browser session.
---

# Chrome CDP

Manage one persistent, locally accessible Chrome debugging instance and reuse it across browser operations.

## Core Capabilities

Develop the skill incrementally in this order:

1. Inspect the regular Chrome installation and list reusable user profiles.
2. Initialize an isolated debug profile by copying one selected regular profile.
3. Manage Chrome CDP lifecycle with `start`, `stop`, and `status`.
4. Provide a Playwright `session()` helper that connects to the running Chrome and disconnects without stopping it.

Do not implement later capabilities while an earlier capability is still being designed or validated.

## Platform Scope

Support:

- macOS
- Ubuntu
- Windows

Keep platform-specific Chrome discovery, process handling, and profile copying behind separate adapters. Keep command behavior and returned status consistent across platforms.

## List Chrome Profiles

Run:

```bash
python3 scripts/chrome_profiles.py
```

Use machine-readable output when another script needs the result:

```bash
python3 scripts/chrome_profiles.py --json
```

The command detects the stable Google Chrome user-data directory on the current platform. It lists normal user profiles such as `Default` and `Profile 2`, while excluding `Guest Profile` and `System Profile`.

Use `--user-data-dir PATH` only when the user explicitly asks to inspect a non-default Chrome installation.

The returned profile name and directory identify a possible source for later debug-profile initialization. Do not infer that encrypted login data can always be copied or reused successfully.

## Copy a Chrome Profile

Close the regular Chrome completely, then run:

```bash
python3 scripts/copy_profile.py "Default"
```

Use a directory returned by `chrome_profiles.py`, such as `Default` or `Profile 2`.

The default target is:

```text
~/chrome-debug-data/
```

Override it only when the user explicitly requests another location:

```bash
python3 scripts/copy_profile.py "Profile 2" \
  --target /path/to/chrome-debug-data
```

The first copy initializes `Local State` and the selected profile directory. Later copies add another profile to the same managed target without overwriting existing profiles. Each newly copied profile becomes the active profile for the next Debug Chrome start.

Later copies merge only the selected profile's `Local State` metadata, preserving state accumulated by Debug Chrome. Profile operations are serialized, and a failed copy rolls back the new profile and metadata.

Before copying, the command checks for a running Chrome process using the current platform's process mechanism. If Chrome is running, request a normal exit and wait up to 15 seconds before copying. If Chrome does not exit or process detection fails, stop without copying. Do not force terminate Chrome.

The command validates the source and target before closing Chrome. If Debug Chrome is running, stop it explicitly first; the copy command does not treat it as the regular source Chrome.

If the target is not managed by `chrome-cdp`, stop and report it. If the selected profile already exists, refuse to overwrite or refresh it.

Profile copying prepares a reusable debug profile but does not guarantee that every encrypted login credential remains usable.

## Manage Debug Chrome

Start the Debug Chrome:

```bash
python3 scripts/chrome_debug.py start
```

Check its state:

```bash
python3 scripts/chrome_debug.py status
```

Stop it:

```bash
python3 scripts/chrome_debug.py stop
```

List copied profiles (`*` marks the active profile; a Chrome display name is shown when available):

```bash
python3 scripts/chrome_debug.py profiles
```

Use `profiles --json` for machine-readable multi-profile output.

Choose the profile for the next start:

```bash
python3 scripts/chrome_debug.py select "Default"
```

The lifecycle command reads `~/chrome-debug-data/.chrome-cdp.json`, starts Chrome with the active copied profile, and exposes CDP only at `127.0.0.1:9222`. Stop Debug Chrome before changing the active profile.

Identify Debug Chrome only when the process executable is the stable Google Chrome,
its `--user-data-dir` exactly matches the managed directory, and it has the expected
main/helper process shape. The owned CDP main process must also use the exact
`--remote-debugging-port=9222` option. On Ubuntu, inspect `/proc/<pid>/exe` and
`/proc/<pid>/cmdline` directly. `stop` requests exit only from a validated Chrome
main process, then waits for its validated helpers to exit. It must not close regular
Chrome or signal an unrelated process that merely carries matching arguments.

`start` is idempotent when the owned Debug Chrome is already running. A running state requires both a working CDP endpoint and an owned main process launched for port `9222`. Refuse to start if the port belongs to another process or if an owned Chrome process exists without a working CDP endpoint. If startup times out, clean up the attempted Debug Chrome before returning an error.

## Safety Boundaries

- Bind CDP to `127.0.0.1`; never expose the debugging port to the network.
- Use a dedicated debug user-data directory; never launch CDP against the regular Chrome data directory.
- Do not commit profiles, cookies, browser storage, logs, process state, or other runtime data.
- Never overwrite an existing debug profile during normal `start`.
- Stop only the Chrome instance owned by this skill.
- Do not print cookies, tokens, passwords, or browser storage contents.
- Treat regular-profile copying as an explicit initialization action, not part of every start.

## Cross-platform Validation

When validating this skill on another device, read this file first and do not modify code during validation.

Use `python3` on macOS and Ubuntu. Use `py` on Windows when `python3` is unavailable. From the skill directory:

1. Run all `test_*.py` unit tests.
2. List regular Chrome profiles in text and JSON formats.
3. Copy one selected profile to the default debug directory.
4. Validate `profiles` and `profiles --json`.
5. Run `start`, `status`, a second idempotent `start`, and `stop`.
6. Confirm CDP listens only on `127.0.0.1:9222`, `stop` leaves no owned process, and regular Chrome is not stopped by Debug Chrome lifecycle commands.
7. Confirm duplicate profile copying, copying while Debug Chrome runs, and profile selection while Debug Chrome runs are rejected safely.

On macOS, Ubuntu, and Windows, verify process ownership, CDP listener ownership, and complete helper-process cleanup. Also confirm that Debug Chrome lifecycle commands do not affect regular Chrome or unrelated processes carrying similar arguments. On Ubuntu and Windows, additionally verify normal Chrome shutdown through the platform-specific implementation.

Report the operating system, Chrome and Python versions, test totals, each failed step with its error output, and an overall pass or fail. Do not inspect or print cookies, tokens, passwords, or browser storage.

## Current Implementation State

Profile discovery, safe one-time profile copying, and Debug Chrome lifecycle management have platform-specific implementations for stable Google Chrome on macOS, Ubuntu, and Windows. All three platform paths have passed automated tests and real-device validation.

The Playwright session helper is not implemented yet.

When asked to use an unimplemented capability, state that it is pending and continue with the next agreed implementation step instead of inventing commands.
