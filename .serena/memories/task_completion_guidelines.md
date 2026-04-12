# Task Completion Guidelines - BTRBK TUI

## Dopo Modifiche al Codice

### Verifica Python
```bash
python3 -m py_compile btrbk_tui.py
python3 -m py_compile btrbk_tui_pro.py
sudo ./btrbk_tui.py
sudo ./btrbk_tui_pro.py
```

### Verifica Rust
```bash
cd btrbk_tui_rust && cargo check
cargo build --release
sudo ./target/release/btrbk_tui
```

## Regole Generali
- Mantenere parità funzionale tra Python TUI Pro e Rust
- Garantire compatibilità schema JSON config condiviso
- Aggiornare README.md per nuove funzionalità
- Verificare permessi eseguibili: `chmod +x *.py`
- Nessun test automatizzato - testing manuale con snapshot btrfs reali
- Tutte le operazioni richiedono root (sudo)
- Verificare che backup `.BROKEN` funzioni correttamente
