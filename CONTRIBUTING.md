# Contributing

This utility edits an undocumented desktop configuration. Contributions that add a supported build must show why the account-scoping and project schemas remain compatible. Include isolated fixtures for new fields, run `python3 -m unittest discover -s scripts -p 'test_*.py' -v`, and verify one real app restart before claiming end-to-end support.

Do not upload real `.codex-global-state.json`, `state_5.sqlite`, backups, run logs, API-key status output, project paths, or chat IDs. Replace real identifiers with synthetic values in tests and issues.

Keep the default behavior read-only until an explicit `restore` or `rollback` command is invoked. Preserve the checks for app version, account mode, conflicting project paths, database mapping, and full app exit. Avoid changing unrelated user settings.
