# Latest Changes - BTRBK TUI v2.7

## v2.7 - Chain-aware purge (2026-08-04, commit 31e4d8f)

### Problema
La purge teneva solo lo snapshot più recente per tipo e cancellava quello che il target di backup usava come **parent** per il prossimo send incrementale. btrbk NON protegge i parent (btrbk.conf(5)) -> il run successivo non trova parent comune e ricade su full send (~900 GiB, oltre un'ora). È così che la catena locale si è rotta il 2026-08-04.

### Soluzione (btrbk_tui_pro.py + Rust; la CLI non ha purge, non toccata)
- Si legge il target da `/etc/btrbk/btrbk.conf` (costante `BTRBK_CONF`): primo target ssh.
- Si interroga il target via ssh: `btrfs subvolume list -u -R` -> insieme dei `received_uuid`.
- Per ogni snapshot locale si legge l'UUID (`btrfs subvolume show`) e lo si confronta con i received_uuid del target.
- Sopravvive lo snapshot più recente presente su entrambi + tutto ciò che è più nuovo; si cancella solo ciò che lo precede.
- **Fail-safe**: target irraggiungibile o nessuno snapshot in comune -> NON si cancella nulla e la UI lo segnala (una catena già rotta non viene peggiorata).
- La status bar annuncia il controllo del target prima che la chiamata ssh blocchi l'interfaccia.
- Rust: helper di parsing come funzioni pure (`parse_target_url`, `parse_received_uuids`, `parse_subvolume_uuid`) + wrapper I/O (`btrbk_target_url`, `target_received_uuids`, `local_subvolume_uuid`).
- Python: `SnapshotManager._get_target_url`, `get_target_received_uuids`, `get_local_uuid`.
- **Trappola nota**: un match approssimativo su `btrfs subvolume show` restituisce "Parent UUID:" invece di "UUID:" (coperta da test).

### Test automatici (novità)
- `btrbk_tui_rust/src/main.rs` ha `mod tests` con 3 unit test: `target_url_is_the_first_ssh_target`, `received_uuids_skip_unset_ones`, `subvolume_uuid_ignores_parent_and_received`. Si eseguono con `cargo test`.
- Commit 3ebb5c4: i campioni nei test usano host/path/UUID **placeholder** (il repo è pubblico: mai IP LAN, path NAS o UUID reali nei test o nel codice).

### Lint ruff fissato nel repo (commit 653c054)
- Senza config, il set di regole dipendeva dalla versione di ruff installata: 51 warning accumulati solo per upgrade del tool.
- `.ruff.toml` fissa la selezione: E, W, F, I, UP, B, C4, SIM, RET, PL, RUF, S, DTZ, EXE (target py39).
- Deroghe documentate inline: PLW1510 (returncode controllati a mano nel percorso restore/rollback), BLE001 (catch ampi per non lasciare il terminale curses inutilizzabile), S110, DTZ005/DTZ007 (nomi snapshot btrbk in ora locale), PLW0603.
- 166 finding corretti senza cambi di comportamento: generics builtin al posto di `typing`, `sys.exit()`, `.values()`, `contextlib.suppress`, variabili spacchettate inutilizzate.

### Desktop entry (commit d952dc5)
- `btrbk-tui.desktop` era un symlink rotto verso la home dell'autore; ora è un file reale: `Exec=pkexec /usr/local/bin/btrbk_tui`, `Terminal=true`. Passa `desktop-file-validate`. Installazione documentata nel README (`install -Dm644 ... ~/.local/share/applications/`).

## Versioni Precedenti
- v2.6 (2026-06-24): audit hardening - rollback verificato con 3 esiti (success/failed/rollback_failed), pre-check sorgente, guardia `btrfs subvolume show`, CLI in parità (verify+rollback, config condivisa, root check, sync), `truncate_str` UTF-8 safe, `render_output_area`, cache snapshot (invalidata su R/restore/purge/clean/create/edit path), zero warning clippy, hook ruff
- v2.6 (2026-04-12): fix catch-all verify, messaggi status, parser ANSI, parità verify Python/Rust
- v2.5: Interfaccia adattiva, colonne dinamiche, rinomina file
- v2.2: Fix timestamp, .BROKEN conflicts, comando B, logica dinamica
