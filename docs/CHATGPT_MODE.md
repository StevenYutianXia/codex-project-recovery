# ChatGPT-account project recovery

The reviewed desktop build stores local project definitions in `local-projects` and chat associations in `thread-project-assignments`. `electron-account-renderer-state-v1` has an active `owner` and inactive account partitions. The active account's project values live at the top level of `.codex-global-state.json`; inactive accounts retain `globals` and `atoms` under their opaque partition IDs. The tool copies only the local project fields needed for restoration, rather than copying a whole account's settings.

## Snapshot contents

- Local project ID, name, root paths, and the existing creation/update fields.
- Local chat-to-project assignments for chats still present in the local SQLite database.
- The app-server mapping between legacy local project IDs and database project IDs, plus project order, appearance, and sidebar order.
- No login credentials, chat messages, cloud projects, or project files.

The local database is opened read-only. Project identities and names must agree between the snapshot and database. Missing chats are skipped and counted; conflicting assignments stop the plan. An existing project with the same folder under a different ID also stops the plan. Unrelated current-account settings and other account partitions are preserved.

## Restore lifecycle

`plan` computes a merge without writing. `restore` starts a one-shot worker that waits for the desktop process to exit and the configuration file to remain unchanged for a short interval. It saves the entire prior file as `before.json`, writes the merged state atomically, verifies the write, then creates a completion note. The worker stops after two hours if the app does not fully exit. Reopening too quickly keeps it waiting.

`status` reports the worker phase. `verify` checks that the selected snapshot is represented in the current configuration. Both are **configuration checks**, so confirm the project list and representative chat memberships in the desktop app after restart.

## Rollback

The ordinary account tool offers `rollback --run /path/to/run` only if the post-restore configuration byte hash still matches the recorded result and the same account is active. If the app has created new settings or chats, this strict rollback stops; compare the saved `before.json` with current state and merge only the needed fields. Do not overwrite newer data with a full old file.

## Version boundary

The ordinary account write path was validated on desktop build 25241. The storage format is internal and can change. On another build, review the installed app's schema and behavior before changing the allowlist. API-key sign-in follows the separate [API workflow](API_MODE.md).
