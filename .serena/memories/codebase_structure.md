# Struttura Codebase - BTRBK TUI v2.6

## Layout Directory
```
btrbk_tui/
├── README.md
├── btrbk_tui.py                  # CLI semplice (~230 righe, ora con verify+rollback+config condivisa)
├── btrbk_tui_pro.py              # Python TUI professionale (~1000 righe)
├── btrbk_tui_rust/               # Rust TUI
│   ├── Cargo.toml               # edition 2024, v2.6.0
│   ├── src/main.rs              # ~1230 righe
│   └── target/release/btrbk_tui
├── .claude/settings.json        # hook PostToolUse: ruff check su file .py (committato)
├── .kiro/settings/mcp.json
└── .serena/
```

## `btrbk_tui.py` (CLI)
- Shebang: `#!/usr/bin/python`
- Imports: `json`, `os`, `subprocess`, `datetime`, `pathlib`
- `CONFIG_FILE` + `load_config()`: legge la config condivisa ~/.config/btrbk_tui/config.json
- Funzioni: `load_config`, `get_snapshot_groups`, `format_snapshot_name`, `display_snapshots`, `verify_restore_success`, `restore_snapshot`, `main`
- `main()`: root check (geteuid) + load_config()
- restore_snapshot: pre-check source, guardia btrfs subvolume show, verify+rollback verificato, sync prima di reboot

## `btrbk_tui_pro.py` (Python TUI)
- Classi: `Config`, `SnapshotManager`, `TUIApp`
- `SnapshotManager` metodi: `get_snapshots`, `format_snapshot_name`, `restore_snapshot` (ritorna "success"|"failed"|"rollback_failed"), `_verify_restore_success`, `purge_old_snapshots`, `clean_broken_subvolumes`
- `TUIApp` metodi: `get_snapshots_cached`/`invalidate_snapshots` (cache), `init_colors`, `draw_*`, `set_status`, `create_snapshot`, `purge_old_snapshots`, `clean_broken_subvolumes`, `edit_setting`, `confirm_dialog`, `handle_main/snapshot_selection/settings_input`, `run`

## `btrbk_tui_rust/src/main.rs` (Rust TUI)
- type alias `SnapshotData = (HashMap<String, Vec<String>>, Vec<String>)`
- enum `RestoreOutcome { Success, Failed, RollbackFailed }`
- Struct: `Config` (6 campi), `App` (campo extra `snapshots_cache: Option<SnapshotData>`)
- `impl App`: `new`, `snapshots_cached`/`invalidate_snapshots`, `load/save_config`, `get_snapshots`, `format_snapshot_name`, `init_colors`, `set_status`, `create_snapshot`, `purge_old_snapshots`, `clean_broken_subvolumes`, `draw_*` (draw_main_screen ora &mut self), `confirm_dialog`, `restore_snapshot` (-> RestoreOutcome), `verify_restore_success`, `handle_*`, `edit_setting`, `toggle_setting`, `run`
- Globali: `truncate_str` (troncamento UTF-8 safe), `render_output_area`, `run_command`, `get_max_yx`, `main`

## Logica Restore (identica Rust/Python/CLI)
1. pre-check: source deve esistere
2. se current esiste: `btrfs subvolume show` (guardia) poi `mv` current -> .BROKEN.TIMESTAMP
3. `btrfs subvolume snapshot` -> nuovo subvolume; se fallisce rollback (mv .BROKEN indietro), esito verificato
4. `verify_restore_success`: root=etc/usr/var/bin+fstab/passwd, home=non vuota, altri=leggibile
5. se verifica fallisce -> rollback completo (delete + mv), esito verificato
6. esiti: success / failed (rollback OK) / rollback_failed (stato incoerente, .BROKEN conservato)
7. se auto_cleanup e current esisteva -> delete .BROKEN

## Config Condivisa
- Path: `~/.config/btrbk_tui/config.json`
- Campi: btr_pool_dir, snapshots_dir, auto_cleanup, confirm_actions, show_timestamps, theme
- Tutte e tre le versioni la leggono (CLI inclusa da v2.6)
