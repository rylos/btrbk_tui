# Latest Changes - BTRBK TUI v2.8

## v2.8 - Audit della TUI Rust (2026-09-21)

`btrbk_tui` (symlink /usr/local/bin -> target/release) è la versione usata ogni giorno: main.rs riscritto in modo coerente (~1850 righe, 13 unit test). Dettaglio completo nel README, sezione v2.8.

### Sicurezza
- **`@root` veniva ripristinato sopra `@`** (Rust + Python TUI): il subvolume era derivato da un "tipo" e il tipo di `@` era "root". Ora `restore_snapshot(snapshot, subvol_name)` riceve il prefisso così com'è; verify fa match su "@" / "@home". La CLI era già corretta.
- ESC durante `btrbk run`: btrbk gira in una sessione propria (`setsid` in pre_exec, stdin null); ESC manda SIGINT al gruppo, SIGKILL dopo 5 s, e SIGKILL finale ai processi rimasti. Prima si uccideva solo btrbk lasciando orfani btrfs send/ssh come root.
- `fs::rename` al posto di `mv` (tra filesystem diversi fallisce invece di copiare); controllo che .BROKEN non esista già.
- Fallback config `/root` e non `/tmp`; panic hook che chiama `endwin()`.

### Bug
- I messaggi "busy" non venivano mai disegnati (set_status + refresh senza draw) -> `show_busy()` ridisegna prima di bloccare.
- Senza snapshot ogni tasto era ignorato (anche S e I).
- Selezione fuori schermo: ora la colonna selezionata scorre (`[5-7 of 12]`), `clamp_selection`, Home/End.
- Output btrbk diviso solo su `\n`: progress con `\r` non live. Ora `forward_stream` divide su entrambi e `OutputLog` sostituisce le righe transitorie.
- Race che saltava la schermata "press any key"; ESCDELAY a 25 ms; header fermo a v2.6 (ora `CARGO_PKG_VERSION`).
- `#[serde(default)]` su Config; salvataggi config falliti segnalati.

### Migliorie
- Purge: prima controlla il target, poi chiede conferma col numero reale; segnala cancellazioni fallite e subvolumi saltati. `sudo btrbk_tui --purge-plan` = dry run (anche `--version`, `--help`).
- `parse_ssh_target`: onora ssh_identity/ssh_user/ssh_port di btrbk.conf, IPv6, `target send-receive ssh://`.
- `run_command -> Result<(), String>` (ultima riga di stderr): gli errori dicono il perché. `RestoreOutcome::Failed(String)` / `RollbackFailed(String)`.
- `erase()` al posto di `clear()` (niente flicker); tutto il disegno passa da `put()` che ritaglia -> nessun panic a qualsiasi dimensione del terminale.
- Status a durata fissa (`Instant`, costanti `STATUS_*`), `Screen` enum, `key_char()` al posto dei codici numerici, cache `Rc<SnapshotGroups>`.
- Timestamp: formati short/long/long-iso e suffisso `_N`; nome snapshot diviso all'ULTIMO punto (`split_snapshot_name`).
- Messaggi di esito in ASCII (`[OK]`/`[FAILED]`): sotto pkexec il locale è C.

### Come è stato verificato (riutilizzabile)
- Restore/rollback/clean: btrfs in loopback (truncate + mkfs.btrfs + mount -o loop), config via `sudo env HOME=<dir>`, TUI pilotata con `tmux send-keys` / `capture-pane`.
- Creazione snapshot e ESC: finto `btrbk` in testa al PATH (`sudo env PATH=...`).
- Purge: `--purge-plan` contro il target reale (sola lettura).

### TUI Python portata in parità (stesso giorno)
`btrbk_tui_pro.py` riscritto come port 1:1 della versione Rust: stesse funzioni pure a livello di modulo (`clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, ...), `OutputLog`, `StreamSplitter`, `put()`, `show_busy`, `clamp_selection`, scroll, `--purge-plan`/`--version`/`--help`, `os.rename`, `start_new_session=True` + `os.killpg`.
- Bug solo Python corretti: `process.stdout.readline()` bloccava l'interfaccia (ESC ignorato fino alla riga successiva) -> ora `select` + `os.read` non bloccanti; dopo la creazione snapshot `nodelay(False)` faceva perdere il `timeout(100)` del loop principale (status congelati finché non si premeva un tasto).
- `restore_snapshot` ritorna `(outcome, reason)`; `plan_purge` solleva `PurgeError`; `run_command` ritorna `None` o il motivo.
- Test: `tests/parsing_test.py` (13 casi speculari a quelli Rust). ATTENZIONE: `.gitignore` esclude `test_*`, per questo il file NON si chiama `test_parsing.py`; si lancia con `python3 -m unittest discover -s tests -p '*_test.py'`.
- La CLI (`btrbk_tui.py`) è invariata a parte la stringa di versione: non ha purge né creazione snapshot, e il mapping del prefisso era già corretto.

## v2.7

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
