# BTRBK TUI v2.10 - Project Overview

## Purpose
Complete toolset for restoring Btrfs snapshots created with btrbk. Three implementations with different interfaces, a shared configuration and feature parity.

### Available Versions
- **`btrbk_tui.py`** - simple CLI with a numbered menu
- **`btrbk_tui_pro.py`** - professional Python TUI with curses
- **`btrbk_tui_rust/`** - high-performance Rust TUI (identical to Pro)

### TUI Commands (Main Screen)
- **↑↓**: Navigate snapshots | **←→**: Switch column
- **ENTER**: Select and restore | **S**: Settings | **R**: Refresh
- **I**: Create snapshot (`btrbk run --progress`) | **P**: Purge OLD | **B**: Clean BROKEN
- **H**: Reboot (after restore) | **Q**: Quit

### TUI Commands (Settings)
- **↑↓**: Navigate | **ENTER**: Edit string | **SPACE**: Toggle boolean
- **S**: Manual save | **ESC**: Back to main

### Key Features
- Dynamic detection of any @prefix
- Adaptive columns, sorted with @ first then alphabetically
- Shared config `~/.config/btrbk_tui/config.json`
- Safe restore: mv → snapshot → verify → rollback on failure
- Unique `.BROKEN.TIMESTAMP` backups
- Specific status messages for every operation
- Chain-aware purge (v2.7, Python and Rust TUIs only): queries btrbk's ssh target and never deletes the parent snapshot of the next incremental send; fail-safe if the target is unreachable (see `mem:latest_changes`)
- Desktop entry `btrbk-tui.desktop` (pkexec on the Rust TUI)
- Public repo on GitHub: no real network/host data in code or tests

- `--purge-plan` (purge dry run) in both TUIs; Home/End; column scrolling
- Daily use: `sudo btrbk_tui` = Rust TUI (symlink in /usr/local/bin)

- Snapshots recognised with or without `@` (btrbk timestamp after the last dot); `--config FILE`

### Status: All versions ✅ Production v2.10 (2026-10-26)
- v2.10: root-only hooks in `/etc/btrbk_tui/post-change.d/` after purge and `btrbk run` (see `mem:latest_changes`)

### External documentation
The author's private Joplin wiki has a dedicated note on this tool (notebook Backup & Storage, id `5e37ad8b6c5542138e3d94ce6903de3c`). Update it when keys, shell options or the restore/purge flow change (then `wiki log` and `wiki brief --write`).
