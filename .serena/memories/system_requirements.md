# System Requirements - BTRBK TUI v2.5

## Requisiti Sistema
- Linux con filesystem Btrfs
- Root access (sudo) per operazioni btrfs
- `btrfs-progs`, `btrbk`

## Python
- Python 3.x (stdlib: `curses`, `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`, `typing`)

## Rust (Cargo.toml)
```toml
[dependencies]
ncurses = "5.101.0"
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
chrono = { version = "0.4", features = ["serde"] }
dirs = "5.0"
libc = "0.2"
```
- Rust edition 2024, rustc 1.87.0+

## Percorsi Runtime
- Config: `~/.config/btrbk_tui/config.json`
- Pool default: `/mnt/btr_pool`
- Snapshots default: `/mnt/btr_pool/btrbk_snapshots`
