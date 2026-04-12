# Struttura Codebase - BTRBK TUI v2.6

## Layout Directory
```
btrbk_tui/
├── README.md
├── btrbk_tui.py                  # CLI semplice (177 righe)
├── btrbk_tui_pro.py              # Python TUI professionale (~1000 righe)
├── btrbk_tui_rust/               # Rust TUI
│   ├── Cargo.toml               # edition 2024, v2.6.0
│   ├── src/main.rs              # ~1200 righe
│   └── target/release/btrbk_tui
├── .kiro/settings/mcp.json
└── .serena/
```

## `btrbk_tui.py` (CLI)
- Shebang: `#!/usr/bin/python`
- Imports: `os`, `subprocess`, `datetime`
- Percorsi hardcoded (non configurabili)
- Funzioni: `get_snapshot_groups`, `format_snapshot_name`, `display_snapshots`, `restore_snapshot`, `main`

## `btrbk_tui_pro.py` (Python TUI)
- Classi: `Config`, `SnapshotManager`, `TUIApp`
- `SnapshotManager` metodi: `get_snapshots`, `format_snapshot_name`, `restore_snapshot`, `_verify_restore_success`, `purge_old_snapshots`, `clean_broken_subvolumes`
- `TUIApp` metodi: `init_colors`, `draw_header/footer/status`, `set_status`, `create_snapshot`, `purge_old_snapshots`, `clean_broken_subvolumes`, `draw_main/settings_screen`, `edit_setting`, `confirm_dialog`, `handle_main/snapshot_selection/settings_input`, `run`

## `btrbk_tui_rust/src/main.rs` (Rust TUI)
- Struct: `Config` (6 campi), `App` (8 campi)
- `impl App`: `new`, `load/save_config`, `get_snapshots`, `format_snapshot_name`, `init_colors`, `set_status`, `create_snapshot`, `purge_old_snapshots`, `clean_broken_subvolumes`, `draw_header/footer/status/main_screen/settings_screen`, `confirm_dialog`, `restore_snapshot`, `verify_restore_success`, `handle_main_input/snapshot_selection/settings_input`, `edit_setting`, `toggle_setting`, `run`
- Globali: `run_command`, `get_max_yx`, `main`

## Logica Restore (identica Rust/Python)
1. `mv` subvolume corrente → `.BROKEN.TIMESTAMP`
2. `btrfs subvolume snapshot` → nuovo subvolume
3. Se snapshot fallisce → rollback (mv .BROKEN indietro)
4. `verify_restore_success`: root=etc/usr/var/bin+fstab/passwd, home=non vuota, altri=leggibile
5. Se verifica fallisce → rollback completo (delete + mv)
6. Se auto_cleanup → delete .BROKEN

## Config Condivisa
- Path: `~/.config/btrbk_tui/config.json`
- Campi: btr_pool_dir, snapshots_dir, auto_cleanup, confirm_actions, show_timestamps, theme
