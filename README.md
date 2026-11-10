# Codex Local Project Recovery

An unofficial, local-only recovery tool for **Codex desktop Projects** that disappear after switching a ChatGPT account or signing in with an API key. It backs up local project entries and existing chat-to-project associations, then restores them without moving project files or copying chat content.

[简体中文说明](README.zh-CN.md)

## Scope and compatibility

- macOS Codex desktop app (`/Applications/ChatGPT.app`), Python 3.10 or newer; no third-party Python packages.
- ChatGPT-account recovery was validated on app build **25241**. API-key recovery was validated on build **25894**. The two workflows have separate version guards. Unsupported builds stop before writing; review the installed app's data model before adding support.
- The tool reads `~/.codex/.codex-global-state.json` and `state_5.sqlite` (SQLite is read-only). It never reads auth files, API keys, or chat bodies. It does **not** restore cloud Projects, cloud chats, or missing project files.
- This project uses an undocumented desktop storage format. Back up your Codex data before using it. See [Security and data handling](SECURITY.md).

## Quick start

```bash
git clone <your-fork-or-repository-url>
cd codex-project-recovery
python3 scripts/project_recovery.py inspect
```

The repository has **no user snapshots or credentials**. Runtime backups and logs go to `~/.codex-project-recovery/`, outside the checkout. Set `CODEX_HOME`, `CODEX_RECOVERY_DATA`, or `CODEX_RECOVERY_APP` if your paths differ. Do not commit that runtime directory.

### Switching between ChatGPT accounts

Before switching:

```bash
python3 scripts/project_recovery.py backup
```

After signing in to the destination account:

```bash
python3 scripts/project_recovery.py plan
python3 scripts/project_recovery.py restore
```

**Fully quit the desktop app from its menu. Wait for the dated completion note on your Desktop (or in the run directory) before reopening.** Closing the window is insufficient. Reopening immediately can leave the worker waiting. Then run `status` and `verify`, and confirm Projects and chat associations in the app.

```bash
python3 scripts/project_recovery.py status
python3 scripts/project_recovery.py verify
```

### Switching to API-key sign-in

Confirm API-key authentication with `codex login status`. The API workflow checks this without printing the status line, which may contain part of the key. It requires an existing project snapshot and an empty API-mode project list.

```bash
python3 scripts/api_project_restore.py plan
python3 scripts/api_project_restore.py restore
```

Fully quit the app, wait for the completion note, then reopen and check Projects and chats in the app. `status` only reports the write phase:

```bash
python3 scripts/api_project_restore.py status
python3 scripts/api_project_restore.py backup  # refresh the snapshot after successful verification
```

API recovery removes the desktop's account-scoping key from the active configuration on the reviewed build. The full previous state, including inactive account partitions, is saved as `before.json` in the run directory. Preserve it when switching back to ChatGPT authentication. See [API-mode details](docs/API_MODE.md).

To use an older known snapshot, add `--snapshot /absolute/path/to/backup.json` to `plan` and `restore`. Compare its project names and paths before writing.

## Verification and limits

`applied_needs_app_verification` means the configuration write succeeded; it does **not** mean the sidebar is restored. Check the app's project list, representative chats in different projects, and external-drive paths. A project entry can be restored while its drive is disconnected, but its files will remain unavailable until the drive is mounted.

The worker waits up to two hours for a stable, fully closed app. It never force-quits the app and does not install a background service. Conflicts, missing database identities, unsupported formats, or unreviewed builds stop the operation.

The account-scoped format, migration fields, and safe rollback rules are described in [ChatGPT account details](docs/CHATGPT_MODE.md).

Run isolated tests with:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

## License

MIT. This is a community utility, not an official OpenAI product.
