# Comandi Suggeriti - BTRBK TUI v2.9

## Esecuzione
```bash
sudo btrbk_tui                                         # Rust TUI (symlink in /usr/local/bin)
sudo ./btrbk_tui.py                                    # CLI
sudo ./btrbk_tui_pro.py                                # Python TUI Pro
```

## Build Rust (aggiorna automaticamente il symlink)
```bash
cd btrbk_tui_rust
cargo check
cargo build --release
# symlink: /usr/local/bin/btrbk_tui -> target/release/btrbk_tui
```

## Lint / Verifica
```bash
# Python (ruff installato via pacman)
python3 -m py_compile btrbk_tui.py btrbk_tui_pro.py
ruff check btrbk_tui.py btrbk_tui_pro.py
ruff check --fix btrbk_tui.py btrbk_tui_pro.py
ruff format btrbk_tui.py btrbk_tui_pro.py
python3 -m unittest discover -s tests -p '*_test.py'   # test Python

# Rust
cd btrbk_tui_rust && cargo clippy --release        # zero warning attualmente
cargo clippy --fix --release --allow-dirty         # applica fix automatici
cargo test                                         # unit test parsing (purge chain-aware)
```

Le regole ruff sono fissate in `.ruff.toml` (il risultato non dipende più dalla versione di ruff installata).

## Desktop entry
```bash
desktop-file-validate btrbk-tui.desktop
install -Dm644 btrbk-tui.desktop ~/.local/share/applications/btrbk-tui.desktop
update-desktop-database ~/.local/share/applications
```

NOTA: un hook Claude Code (.claude/settings.json) esegue automaticamente `ruff check`
dopo ogni Edit/Write/MultiEdit su file .py e reinietta gli errori all'agente.

## Dry run della purge (sola lettura, interroga il target via ssh)
```bash
sudo btrbk_tui --purge-plan
sudo ./btrbk_tui_pro.py --purge-plan
```

## Sistema
```bash
ls -la /mnt/btr_pool/btrbk_snapshots/
cat ~/.config/btrbk_tui/config.json
```

## Release GitHub
```bash
V=2.9.0; B=btrbk_tui-$V-x86_64-linux
cp btrbk_tui_rust/target/release/btrbk_tui $B && strip $B && sha256sum $B > $B.sha256
# note: sezione "Features vX.Y" del README
gh release create v$V --target main --title "v$V - ..." --notes-file notes.md $B $B.sha256
```
Versione da allineare prima: Cargo.toml, VERSION in btrbk_tui_pro.py, README.
