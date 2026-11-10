# API-key mode: why this needs a separate workflow

On reviewed desktop build 25894, API-key authentication has no ChatGPT account principal for the renderer. An existing `electron-account-renderer-state-v1` with `owner: null` causes the desktop's `selectRendererOwner(null)` path to clear top-level account-scoped project values during startup. Merely writing old projects back under that key does not survive a restart.

The API workflow verifies the app build, API-key login status, snapshot identities, database mapping, and empty project state. Once the app is fully closed and its configuration stable, it saves the **entire previous configuration** in a private `before.json`, merges known local project entries and chat associations into top-level values, and omits the account-scoping key in the new configuration. This uses the unscoped local configuration path for that reviewed build.

The worker waits two hours at most. Reopening the app before its completion note appears can leave it waiting. After the note, reopen and verify in the app. A successful file write alone is not proof of restored Projects.

## Returning to ChatGPT sign-in

The previous account partitions remain in `before.json` rather than in the active API-mode configuration. Preserve every run directory and the selected-field snapshots in `backups/`. Switching sign-in method can create fresh account-scoped state; inspect the new state and use the ordinary account recovery flow only after validating its build and schema. Do not restore an entire old configuration over newly created chats or settings.

## Review boundary

This is a build-specific workaround, not a stable import API. On an unreviewed build, the script stops before writing. A maintainer should inspect the installed app's account-scoping behavior, project schema, and migration state, then test with isolated fixtures and a real app restart before expanding the allowlist.
