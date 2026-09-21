# Task Completion Guidelines - BTRBK TUI

## Dopo Modifiche al Codice

### Verifica Python
```bash
python3 -m py_compile btrbk_tui.py btrbk_tui_pro.py
ruff check btrbk_tui.py btrbk_tui_pro.py     # regole fissate in .ruff.toml, deve essere pulito
sudo ./btrbk_tui.py
sudo ./btrbk_tui_pro.py
```

### Verifica Rust
```bash
cd btrbk_tui_rust && cargo check
cargo clippy --release        # zero warning
cargo test                    # unit test delle funzioni di parsing (purge chain-aware)
cargo build --release
sudo ./target/release/btrbk_tui
```

## Regole Generali
- Mantenere parità funzionale tra Python TUI Pro e Rust (la CLI non ha purge)
- Garantire compatibilità schema JSON config condiviso
- Aggiornare README.md per nuove funzionalità
- Verificare permessi eseguibili: `chmod +x *.py`
- Test automatici solo per il parsing Rust (`mod tests`); il resto è testing manuale con snapshot btrfs reali. Logica nuova di parsing -> funzione pura + unit test
- **Repo pubblico**: nei test e nel codice usare solo host/path/UUID placeholder, mai IP LAN, path NAS o UUID reali
- Nuove deroghe ruff: documentarle inline in `.ruff.toml`, non lasciarle implicite
- Tutte le operazioni richiedono root (sudo)
- Verificare che backup `.BROKEN` funzioni correttamente
- La purge non deve mai cancellare il parent snapshot del target (vedi `mem:latest_changes`)
