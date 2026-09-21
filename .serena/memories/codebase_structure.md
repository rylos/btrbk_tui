# Struttura Codebase - BTRBK TUI v2.7

## Layout Directory
```
btrbk_tui/
├── README.md
├── btrbk_tui.py                  # CLI semplice (~260 righe, verify+rollback+config condivisa, NO purge)
├── btrbk_tui_pro.py              # Python TUI professionale (~1090 righe)
├── btrbk_tui_rust/               # Rust TUI
│   ├── Cargo.toml               # edition 2024, v2.7.0
│   ├── src/main.rs              # ~1440 righe, include mod tests
│   └── target/release/btrbk_tui
├── btrbk-tui.desktop            # desktop entry reale (pkexec /usr/local/bin/btrbk_tui)
├── .ruff.toml                   # regole ruff fissate + deroghe documentate
├── .claude/settings.json        # hook PostToolUse: ruff check su file .py (committato)
├── .mcp.json
├── .kiro/settings/mcp.json
└── .serena/
```

## `btrbk_tui.py` (CLI)
- Shebang: `#!/usr/bin/python`
- Imports: `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`
- `CONFIG_FILE` + `load_config()`: legge la config condivisa ~/.config/btrbk_tui/config.json
- Funzioni: `load_config`, `get_snapshot_groups`, `format_snapshot_name`, `display_snapshots`, `verify_restore_success`, `restore_snapshot`, `main`
- `main()`: root check (geteuid) + load_config()
- restore_snapshot: pre-check source, guardia btrfs subvolume show, verify+rollback verificato, sync prima di reboot

## `btrbk_tui_pro.py` (Python TUI)
- Costanti: `CONFIG_FILE`, `BTRBK_CONF` (= /etc/btrbk/btrbk.conf), `DEFAULT_CONFIG`
- Classi: `Config`, `SnapshotManager`, `TUIApp`
- `SnapshotManager` metodi: `get_snapshots`, `format_snapshot_name`, `restore_snapshot` (ritorna "success"|"failed"|"rollback_failed"), `_verify_restore_success`, `_get_target_url`, `get_target_received_uuids`, `get_local_uuid`, `purge_old_snapshots` (chain-aware), `clean_broken_subvolumes`
- `TUIApp` metodi: `get_snapshots_cached`/`invalidate_snapshots` (cache), `init_colors`, `draw_*`, `set_status`, `create_snapshot`, `purge_old_snapshots`, `clean_broken_subvolumes`, `edit_setting`, `confirm_dialog`, `handle_main/snapshot_selection/settings_input`, `run`

## `btrbk_tui_rust/src/main.rs` (Rust TUI)
- const `BTRBK_CONF`
- type alias `SnapshotData = (HashMap<String, Vec<String>>, Vec<String>)`
- enum `RestoreOutcome { Success, Failed, RollbackFailed }`
- Struct: `Config` (6 campi), `App` (campo extra `snapshots_cache: Option<SnapshotData>`)
- `impl App`: `new`, `snapshots_cached`/`invalidate_snapshots`, `load/save_config`, `get_snapshots`, `format_snapshot_name`, `init_colors`, `set_status`, `create_snapshot`, `purge_old_snapshots` (chain-aware), `clean_broken_subvolumes`, `draw_*` (draw_main_screen è &mut self), `confirm_dialog`, `restore_snapshot` (-> RestoreOutcome), `verify_restore_success`, `handle_*`, `edit_setting`, `toggle_setting`, `run`
- Globali: `truncate_str` (troncamento UTF-8 safe), `render_output_area`, `run_command`, `get_max_yx`, `main`
- Purge chain-aware: parsing puro `parse_target_url`, `parse_received_uuids`, `parse_subvolume_uuid`; wrapper I/O `btrbk_target_url`, `target_received_uuids`, `local_subvolume_uuid`
- `mod tests`: 3 unit test sulle funzioni di parsing (`cargo test`)

## Logica Restore (identica Rust/Python/CLI)
1. pre-check: source deve esistere
2. se current esiste: `btrfs subvolume show` (guardia) poi `mv` current -> .BROKEN.TIMESTAMP
3. `btrfs subvolume snapshot` -> nuovo subvolume; se fallisce rollback (mv .BROKEN indietro), esito verificato
4. `verify_restore_success`: root=etc/usr/var/bin+fstab/passwd, home=non vuota, altri=leggibile
5. se verifica fallisce -> rollback completo (delete + mv), esito verificato
6. esiti: success / failed (rollback OK) / rollback_failed (stato incoerente, .BROKEN conservato)
7. se auto_cleanup e current esisteva -> delete .BROKEN

## Logica Purge chain-aware (Rust + Python TUI, v2.7)
1. target ssh letto da `BTRBK_CONF`; `btrfs subvolume list -u -R` via ssh -> received_uuid
2. UUID di ogni snapshot locale confrontato con i received_uuid
3. si conserva il più recente presente su entrambi + tutti i più nuovi; si cancella solo ciò che precede
4. fail-safe: target irraggiungibile o nessun snapshot comune -> nessuna cancellazione, segnalato in UI
Dettagli e motivazione: `mem:latest_changes`

## Config Condivisa
- Path: `~/.config/btrbk_tui/config.json`
- Campi: btr_pool_dir, snapshots_dir, auto_cleanup, confirm_actions, show_timestamps, theme
- Tutte e tre le versioni la leggono (CLI inclusa da v2.6)
