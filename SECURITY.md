# Security and private data

This tool does not inspect auth files or copy API keys or chat bodies. Its snapshots **do** contain local folder paths and chat IDs. Each recovery run also saves a full `before.json` of the desktop configuration, which can contain additional private settings. Keep the runtime data directory (`~/.codex-project-recovery/` by default) private and outside Git. The scripts create backups and run files with owner-only permissions.

Before publishing a fork, inspect `git status`, `git ls-files`, and search tracked files for your username, home path, tokens, chat IDs, and project names. Never commit `~/.codex`, snapshots, run directories, screenshots, or logs. If private data reaches a remote repository, removing it in a later commit is insufficient; rotate affected credentials if any and remove the data from Git history.

The restore workers write the desktop configuration only after the app has fully exited and the file has remained stable. They stop on unsupported builds, malformed data, conflicting project roots or chat assignments, and changed account state. Do not bypass these checks without reviewing the installed app's behavior.

To report a security issue, use GitHub private vulnerability reporting if the published repository enables it. Do not attach real configuration files or API keys to public issues.
