# Comandi Suggeriti - BTRBK TUI v2.6

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

## Verifica Sintassi Python
```bash
python3 -m py_compile btrbk_tui.py
python3 -m py_compile btrbk_tui_pro.py
```

## Sistema
```bash
ls -la /mnt/btr_pool/btrbk_snapshots/
cat ~/.config/btrbk_tui/config.json
```
