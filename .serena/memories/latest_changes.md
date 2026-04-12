# Latest Changes - BTRBK TUI v2.6

## v2.6 - Audit Bug Fixes (2026-04-12)

### Bug Critici Risolti (Rust + Python)
1. **`verify_restore_success` catch-all `_ => return false`**: Qualsiasi tipo diverso da root/home/games causava rollback. Ora il catch-all verifica solo leggibilità directory → supporta @work, @custom, @var, ecc.
2. **Messaggi status sbagliati**: 21 occorrenze di "Operation cancelled" e "Processing..." usati nel contesto sbagliato. Ora ogni operazione ha il suo messaggio specifico (refresh, purge, clean, restore, toggle, save, ecc.)
3. **`edit_setting` echo()**: Causava doppia visualizzazione caratteri. Rimosso echo()/noecho(), la gestione manuale con mvaddstr è sufficiente.
4. **Escape sequence cleanup incompleto**: `replace('\x1b', "")` rimuoveva solo il byte ESC, non l'intera sequenza ANSI. Ora parser completo che salta ESC[...m.
5. **Restore fallito mostra "Processing..."**: Ora mostra "Error: {type} snapshot restore failed (rolled back)".

### Bug Python-specifici Risolti
6. **`set_status(100, 30)` e `set_status(150, 30)`**: Passavano numeri come messaggio stringa. Tutti sostituiti con messaggi corretti.
7. **`handle_snapshot_selection` struttura if/else rotta**: C'era un `else` dopo un `else` (errore di indentazione). Fixata struttura.
8. **Mancava `verify_restore_success` + rollback**: Python non aveva verifica post-restore né rollback. Aggiunto `_verify_restore_success()` a SnapshotManager con stessa logica Rust.

### Allineamento Versioni
- Tutte e tre le versioni ora a v2.6
- Python TUI e Rust hanno stessa logica di restore: mv → snapshot → verify → rollback se fallisce
- Messaggi status identici tra Python e Rust

## Versioni Precedenti
- v2.5: Interfaccia adattiva, colonne dinamiche, rinomina file
- v2.2: Fix timestamp, .BROKEN conflicts, comando B, logica dinamica
