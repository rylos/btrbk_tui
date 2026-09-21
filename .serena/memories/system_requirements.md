# System Requirements - BTRBK TUI v2.7

## Requisiti Sistema
- Linux con filesystem Btrfs
- Root access (sudo) per operazioni btrfs
- `btrfs-progs`, `btrbk`
- Purge chain-aware: `/etc/btrbk/btrbk.conf` leggibile con un target ssh, e accesso ssh di root al target (senza: la purge non cancella nulla, fail-safe)
- Desktop entry: `pkexec` + agente polkit

## Python
- Python 3.10+ (annotazioni `str | None` valutate a runtime; stdlib: `contextlib`, `curses`, `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`)
- Dev: `ruff` (config in `.ruff.toml`)

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
