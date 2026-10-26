# Suggested Commands - BTRBK TUI v2.10

## Running
```bash
sudo btrbk_tui                                         # Rust TUI (symlink in /usr/local/bin)
sudo ./btrbk_tui.py                                    # CLI
sudo ./btrbk_tui_pro.py                                # Python TUI Pro
```

## Rust build (updates the symlink automatically)
```bash
cd btrbk_tui_rust
cargo check
cargo build --release
# symlink: /usr/local/bin/btrbk_tui -> target/release/btrbk_tui
```

## Lint / Checks
```bash
# Python (ruff installed via pacman)
python3 -m py_compile btrbk_tui.py btrbk_tui_pro.py
ruff check btrbk_tui.py btrbk_tui_pro.py
ruff check --fix btrbk_tui.py btrbk_tui_pro.py
ruff format btrbk_tui.py btrbk_tui_pro.py
python3 -m unittest discover -s tests -p '*_test.py'   # Python tests

# Rust
cd btrbk_tui_rust && cargo clippy --release        # currently zero warnings
cargo clippy --fix --release --allow-dirty         # apply automatic fixes
cargo test                                         # parsing unit tests (chain-aware purge)
```

The ruff rules are pinned in `.ruff.toml` (the result no longer depends on the installed ruff version).

## Desktop entry
```bash
desktop-file-validate btrbk-tui.desktop
install -Dm644 btrbk-tui.desktop ~/.local/share/applications/btrbk-tui.desktop
update-desktop-database ~/.local/share/applications
```

NOTE: a Claude Code hook (.claude/settings.json) runs `ruff check` automatically
after every Edit/Write/MultiEdit on .py files and feeds the errors back to the agent.

## Purge dry run (read only, queries the target over ssh)
```bash
sudo btrbk_tui --purge-plan
sudo ./btrbk_tui_pro.py --purge-plan
```

## System
```bash
ls -la /mnt/btr_pool/btrbk_snapshots/
cat ~/.config/btrbk_tui/config.json
```

## GitHub release
```bash
V=2.10.0; B=btrbk_tui-$V-x86_64-linux
cp btrbk_tui_rust/target/release/btrbk_tui $B && strip $B && sha256sum $B > $B.sha256
# notes: the README's "Features vX.Y" section
gh release create v$V --target main --title "v$V - ..." --notes-file notes.md $B $B.sha256
```
Version to align first: Cargo.toml, VERSION in btrbk_tui_pro.py, README.
