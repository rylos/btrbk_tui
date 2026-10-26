# Task Completion Guidelines - BTRBK TUI

## After Code Changes

### Python checks
```bash
python3 -m py_compile btrbk_tui.py btrbk_tui_pro.py
ruff check .                                 # rules pinned in .ruff.toml, must be clean
python3 -m unittest discover -s tests -p '*_test.py'   # 15 tests, mirroring the Rust ones
sudo ./btrbk_tui.py
sudo ./btrbk_tui_pro.py
```

### Rust checks
```bash
cd btrbk_tui_rust && cargo check
cargo clippy --release        # zero warnings
cargo test                    # unit tests of the parsing functions (chain-aware purge)
cargo build --release
sudo ./target/release/btrbk_tui
```

## General Rules
- Keep feature parity between Python TUI Pro and Rust (the CLI has no purge)
- Keep the shared JSON config schema compatible
- Update README.md for new features
- Check executable permissions: `chmod +x *.py`
- Automated tests on the pure functions, with the SAME cases in Rust (`mod tests`) and Python (`tests/parsing_test.py`): a logic change must be made and tested in both. New logic -> pure function + unit test in both versions
- End-to-end tests without touching the real pool: btrfs on loopback + `--config <file>` for the config, fake `btrbk` at the front of PATH, TUI driven with `tmux send-keys`/`capture-pane`, `--purge-plan` for the purge (see `mem:latest_changes`)
- **Public repo**: use only placeholder hosts/paths/UUIDs in tests and code, never LAN IPs, NAS paths or real UUIDs
- New ruff exceptions: document them inline in `.ruff.toml`, never leave them implicit
- All operations need root (sudo)
- Check that the `.BROKEN` backup works correctly
- The purge must never delete the target's parent snapshot (see `mem:latest_changes`)
