# Task Completion Guidelines - BTRBK TUI

## Dopo Modifiche al Codice

### Verifica Python
```bash
python3 -m py_compile btrbk_tui.py btrbk_tui_pro.py
ruff check .                                 # regole fissate in .ruff.toml, deve essere pulito
python3 -m unittest discover -s tests -p '*_test.py'   # 15 test, speculari a quelli Rust
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
- Test automatici sulle funzioni pure, con gli STESSI casi in Rust (`mod tests`) e Python (`tests/parsing_test.py`): una modifica alla logica va fatta e testata in entrambe. Logica nuova -> funzione pura + unit test nelle due versioni
- Prove end-to-end senza toccare il pool vero: btrfs in loopback + `--config <file>` per la config, finto `btrbk` in testa al PATH, TUI pilotata con `tmux send-keys`/`capture-pane`, `--purge-plan` per la purge (vedi `mem:latest_changes`)
- **Repo pubblico**: nei test e nel codice usare solo host/path/UUID placeholder, mai IP LAN, path NAS o UUID reali
- Nuove deroghe ruff: documentarle inline in `.ruff.toml`, non lasciarle implicite
- Tutte le operazioni richiedono root (sudo)
- Verificare che backup `.BROKEN` funzioni correttamente
- La purge non deve mai cancellare il parent snapshot del target (vedi `mem:latest_changes`)
