# System Requirements - BTRBK TUI v2.7

## System Requirements
- Linux with a Btrfs filesystem
- Root access (sudo) for btrfs operations
- `btrfs-progs`, `btrbk`
- Chain-aware purge: a readable `/etc/btrbk/btrbk.conf` with an ssh target, and root ssh access to the target (without it the purge deletes nothing, fail-safe)
- Desktop entry: `pkexec` + a polkit agent

## Python
- Python 3.10+ (`str | None` annotations evaluated at runtime; stdlib: `contextlib`, `curses`, `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`)
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

## Runtime Paths
- Config: `~/.config/btrbk_tui/config.json`
- Default pool: `/mnt/btr_pool`
- Default snapshots: `/mnt/btr_pool/btrbk_snapshots`
