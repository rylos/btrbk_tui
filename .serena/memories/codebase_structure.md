# Codebase Structure - BTRBK TUI v2.10

## Directory Layout
```
btrbk_tui/
├── README.md
├── btrbk_tui.py                  # simple CLI (~260 lines, verify+rollback+shared config, NO purge)
├── btrbk_tui_pro.py              # professional Python TUI (~1040 lines, 1:1 port of Rust)
├── tests/parsing_test.py         # 16 unittests of the pure Python functions
├── btrbk_tui_rust/               # Rust TUI
│   ├── Cargo.toml               # edition 2024, v2.10.0
│   ├── src/main.rs              # ~1850 lines, includes mod tests
│   └── target/release/btrbk_tui
├── btrbk-tui.desktop            # real desktop entry (pkexec /usr/local/bin/btrbk_tui)
├── .ruff.toml                   # pinned ruff rules + documented exceptions
├── .claude/settings.json        # PostToolUse hook: ruff check on .py files (committed)
├── .mcp.json
├── .kiro/settings/mcp.json
└── .serena/
```

## `btrbk_tui.py` (CLI)
- Shebang: `#!/usr/bin/python`
- Imports: `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`
- `CONFIG_FILE` + `load_config()`: reads the shared config ~/.config/btrbk_tui/config.json
- Functions: `load_config`, `get_snapshot_groups`, `format_snapshot_name`, `display_snapshots`, `verify_restore_success`, `restore_snapshot`, `main`
- `main()`: root check (geteuid) + load_config()
- restore_snapshot: source pre-check, btrfs subvolume show guard, verify + verified rollback, sync before reboot

## `btrbk_tui_pro.py` (Python TUI, v2.8, 1:1 port of the Rust version)
- Constants: `VERSION`, `CONFIG_FILE`, `BTRBK_CONF`, `DEFAULT_CONFIG`, `SETTINGS`, `KEY_ESC`, `ENTER_KEYS`, `BACKSPACE_KEYS`, `STATUS_*` (seconds), `MAX_OUTPUT_LINES`
- Pure functions (tested): `clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, `parse_received_uuids`, `parse_subvolume_uuid`, `key_char`; dataclasses `PurgePlan`, `SshTarget`; classes `OutputLog`, `StreamSplitter`
- I/O: `last_line`, `run_command` (None = ok, otherwise the reason)
- `Config`; `PurgeError`; `SnapshotManager`: `get_snapshots`, `format_snapshot_name`, `restore_snapshot` (-> (outcome, reason)), `_verify_restore_success` (-> reason or None), `get_target_received_uuids`, `get_local_uuid`, `plan_purge`, `execute_purge`, `clean_broken_subvolumes`
- Drawing: `put`, `put_centered`, `put_separator` (module level)
- `TUIApp`: `set_status(msg, seconds)`, `show_busy`, `draw_*`, `draw_screen`, `clamp_selection`, `create_snapshot` (-> None or reason), `render_output_area`, `confirm_dialog`, `edit_setting`, `toggle_setting`, `handle_main_input`, `open_settings`, `handle_refresh/reboot/purge/clean_broken/create_snapshot`, `handle_snapshot_selection`, `handle_settings_input`, `run`
- `print_purge_plan`, `main` (options `--purge-plan`, `--version`, `--help`)

## `tests/parsing_test.py`
16 unittests mirroring the Rust tests. It is not called `test_*.py` because `.gitignore` excludes that pattern.

## `btrbk_tui_rust/src/main.rs` (Rust TUI, v2.8, ~1850 lines)
- Constants: `BTRBK_CONF`, `VERSION` (from Cargo), `KEY_*`, `STATUS_*` (Duration), `SETTINGS`
- Types: `Config` (#[serde(default)]), `SnapshotGroups = Vec<(String, Vec<String>)>` ("@" first, newest on top), `PurgeCandidates`, `RestoreOutcome { Success, Failed(String), RollbackFailed(String) }`, `PurgePlan { delete, skipped }`, `SshTarget`, `Screen { Main, Settings }`, `App`, `OutputLog`
- `impl App`: `new`, `snapshots_cached` (Rc) / `invalidate_snapshots`, `load/save_config`, `get_snapshots`, `format_snapshot_name`, `init_colors`, `set_status`, `show_busy`, `create_snapshot` (-> Result), `plan_purge`, `execute_purge`, `clean_broken_subvolumes`, `draw_header/footer/status/main_screen/settings_screen`, `draw_screen`, `clamp_selection`, `confirm_dialog`, `restore_snapshot(snapshot, subvol_name)`, `verify_restore_success`, `handle_main_input`, `handle_reboot/purge/clean_broken/create_snapshot`, `handle_snapshot_selection`, `handle_settings_input`, `edit_setting`, `toggle_setting`, `run`
- Pure functions (tested): `clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, `parse_received_uuids`, `parse_subvolume_uuid`, `key_char`, `forward_stream`
- I/O: `target_received_uuids` (-> Result), `local_subvolume_uuid`, `run_command` (-> Result<(), String>), `last_line`
- Drawing: `put` (clips at the edges, the only place that calls mvaddstr), `put_centered`, `put_separator`, `truncate_str`, `render_output_area`, `get_max_yx`
- `main`: options `--purge-plan`, `--version`, `--help`; root check; panic hook with endwin; setlocale; set_escdelay(25)
- `mod tests`: 13 unit tests

## Restore Logic (identical in Rust/Python/CLI)
1. pre-check: the source must exist
2. if current exists: `btrfs subvolume show` (guard) then rename(2) (TUI: fs::rename / os.rename; the CLI still uses mv) current -> .BROKEN.TIMESTAMP
3. `btrfs subvolume snapshot` -> new subvolume; if it fails, rollback (mv .BROKEN back), outcome verified
4. `verify_restore_success`: root=etc/usr/var/bin+fstab/passwd, home=not empty, others=readable
5. if verification fails -> full rollback (delete + mv), outcome verified
6. outcomes: success / failed (rollback OK) / rollback_failed (inconsistent state, .BROKEN kept)
7. if auto_cleanup and current existed -> delete .BROKEN

## Chain-aware Purge Logic (Rust + Python TUI, v2.7)
1. ssh target read from `BTRBK_CONF`; `btrfs subvolume list -u -R` via ssh -> received_uuid
2. UUID of each local snapshot compared with the received_uuids
3. keep the newest one present on both + everything newer; delete only what precedes it
4. fail-safe: target unreachable or no common snapshot -> nothing deleted, reported in the UI
Details and rationale: `mem:latest_changes`

## Shared Config
- Lookup (`config_candidates`): `~<user behind sudo/pkexec>/.config/btrbk_tui/config.json`, `/root/.config/btrbk_tui/...`, then the same under `btrbk_restore/` (read only). `--config FILE` skips the lookup. Saved as root into the user's home -> chown to the user
- Fields: btr_pool_dir, snapshots_dir, auto_cleanup, confirm_actions, show_timestamps, theme
- All three versions read it (CLI included since v2.6)
