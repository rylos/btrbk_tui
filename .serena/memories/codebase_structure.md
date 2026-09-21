# Struttura Codebase - BTRBK TUI v2.8

## Layout Directory
```
btrbk_tui/
├── README.md
├── btrbk_tui.py                  # CLI semplice (~260 righe, verify+rollback+config condivisa, NO purge)
├── btrbk_tui_pro.py              # Python TUI professionale (~1040 righe, port 1:1 di Rust)
├── tests/parsing_test.py         # 13 unittest delle funzioni pure Python
├── btrbk_tui_rust/               # Rust TUI
│   ├── Cargo.toml               # edition 2024, v2.8.0
│   ├── src/main.rs              # ~1850 righe, include mod tests
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

## `btrbk_tui_pro.py` (Python TUI, v2.8, port 1:1 della versione Rust)
- Costanti: `VERSION`, `CONFIG_FILE`, `BTRBK_CONF`, `DEFAULT_CONFIG`, `SETTINGS`, `KEY_ESC`, `ENTER_KEYS`, `BACKSPACE_KEYS`, `STATUS_*` (secondi), `MAX_OUTPUT_LINES`
- Funzioni pure (testate): `clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, `parse_received_uuids`, `parse_subvolume_uuid`, `key_char`; dataclass `PurgePlan`, `SshTarget`; classi `OutputLog`, `StreamSplitter`
- I/O: `last_line`, `run_command` (None = ok, altrimenti il motivo)
- `Config`; `PurgeError`; `SnapshotManager`: `get_snapshots`, `format_snapshot_name`, `restore_snapshot` (-> (outcome, reason)), `_verify_restore_success` (-> motivo o None), `get_target_received_uuids`, `get_local_uuid`, `plan_purge`, `execute_purge`, `clean_broken_subvolumes`
- Disegno: `put`, `put_centered`, `put_separator` (a livello di modulo)
- `TUIApp`: `set_status(msg, seconds)`, `show_busy`, `draw_*`, `draw_screen`, `clamp_selection`, `create_snapshot` (-> None o motivo), `render_output_area`, `confirm_dialog`, `edit_setting`, `toggle_setting`, `handle_main_input`, `open_settings`, `handle_refresh/reboot/purge/clean_broken/create_snapshot`, `handle_snapshot_selection`, `handle_settings_input`, `run`
- `print_purge_plan`, `main` (opzioni `--purge-plan`, `--version`, `--help`)

## `tests/parsing_test.py`
13 unittest speculari ai test Rust. Non si chiama `test_*.py` perché `.gitignore` esclude quel pattern.

## `btrbk_tui_rust/src/main.rs` (Rust TUI, v2.8, ~1850 righe)
- Costanti: `BTRBK_CONF`, `VERSION` (da Cargo), `KEY_*`, `STATUS_*` (Duration), `SETTINGS`
- Tipi: `Config` (#[serde(default)]), `SnapshotGroups = Vec<(String, Vec<String>)>` ("@" primo, più recente in testa), `PurgeCandidates`, `RestoreOutcome { Success, Failed(String), RollbackFailed(String) }`, `PurgePlan { delete, skipped }`, `SshTarget`, `Screen { Main, Settings }`, `App`, `OutputLog`
- `impl App`: `new`, `snapshots_cached` (Rc) / `invalidate_snapshots`, `load/save_config`, `get_snapshots`, `format_snapshot_name`, `init_colors`, `set_status`, `show_busy`, `create_snapshot` (-> Result), `plan_purge`, `execute_purge`, `clean_broken_subvolumes`, `draw_header/footer/status/main_screen/settings_screen`, `draw_screen`, `clamp_selection`, `confirm_dialog`, `restore_snapshot(snapshot, subvol_name)`, `verify_restore_success`, `handle_main_input`, `handle_reboot/purge/clean_broken/create_snapshot`, `handle_snapshot_selection`, `handle_settings_input`, `edit_setting`, `toggle_setting`, `run`
- Funzioni pure (testate): `clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, `parse_received_uuids`, `parse_subvolume_uuid`, `key_char`, `forward_stream`
- I/O: `target_received_uuids` (-> Result), `local_subvolume_uuid`, `run_command` (-> Result<(), String>), `last_line`
- Disegno: `put` (ritaglia ai bordi, unico punto che chiama mvaddstr), `put_centered`, `put_separator`, `truncate_str`, `render_output_area`, `get_max_yx`
- `main`: opzioni `--purge-plan`, `--version`, `--help`; root check; panic hook con endwin; setlocale; set_escdelay(25)
- `mod tests`: 13 unit test

## Logica Restore (identica Rust/Python/CLI)
1. pre-check: source deve esistere
2. se current esiste: `btrfs subvolume show` (guardia) poi rename(2) (TUI: fs::rename / os.rename; la CLI usa ancora mv) current -> .BROKEN.TIMESTAMP
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
