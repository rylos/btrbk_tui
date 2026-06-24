# Latest Changes - BTRBK TUI v2.6

## v2.6 - Audit Hardening (2026-06-24)

Audit completo delle 3 versioni + fix di tutti i problemi trovati. Tutto compila pulito (py_compile OK, cargo build --release OK, cargo clippy ZERO warning).

### Restore robusto (Rust + Python TUI + CLI)
- **Rollback verificato**: ora si controlla il returncode di OGNI comando di rollback (mv/btrfs delete). Prima erano ignorati -> potevano lasciare il sistema senza `@` mostrando "rolled back" falsamente.
- **3 esiti distinti**: success / failed (rollback OK) / rollback_failed (stato incoerente, .BROKEN conservato, messaggio CRITICAL timeout 300 / exit 1 nella CLI).
  - Rust: enum `RestoreOutcome { Success, Failed, RollbackFailed }`
  - Python TUI: `restore_snapshot` ritorna stringa "success"|"failed"|"rollback_failed"
  - CLI: messaggi CRITICO con path .BROKEN per recupero manuale
- **Pre-check sorgente**: si verifica esistenza source prima di distruggere il subvolume corrente.
- **Guardia subvolume**: prima del mv distruttivo si verifica `btrfs subvolume show current` (evita di spostare una dir normale).
- **current_existed**: gestito il caso in cui il subvolume corrente non esiste (no mv, no cleanup .BROKEN inutile).

### CLI portata in parità (btrbk_tui.py)
- Aggiunto `verify_restore_success()` + rollback completo (prima era solo mv->snapshot, regressione di sicurezza).
- Aggiunto `load_config()`: legge ~/.config/btrbk_tui/config.json (prima ignorava la config condivisa).
- Aggiunto root check (os.geteuid()).
- sync prima di reboot.

### Rust-specifici
- **Slicing UTF-8 -> panic**: helper `truncate_str(s, max_chars)` (usa chars().take()) sostituisce TUTTI i &s[..n] per byte (~15 occorrenze in draw_*/create_snapshot/confirm_dialog). Prima un carattere multibyte sul punto di taglio causava panic senza endwin().
- Centraggio completion_msg con .chars().count() (i simboli check/cross sono 3 byte).
- `render_output_area()` helper: ridisegna l'intera area output (fix glitch scroll + dedup dei 2 blocchi identici).
- unwrap() su stdout/stderr -> match con early return.
- Type alias `SnapshotData = (HashMap<String, Vec<String>>, Vec<String>)`.

### Cache snapshot (Rust + Python TUI)
- Prima get_snapshots() (read_dir) veniva chiamato ad ogni frame (~100ms) + ad ogni tasto.
- Ora cache invalidata solo su: R (refresh), restore, purge, clean, create, edit path.
  - Rust: campo snapshots_cache: Option<SnapshotData>, snapshots_cached()/invalidate_snapshots(), draw_main_screen ora &mut self.
  - Python: _snapshot_cache, get_snapshots_cached()/invalidate_snapshots().
- Tasto R ora invalida davvero la cache (prima era solo cosmetico).

### Pulizia
- Python: rimosso codice morto in create_snapshot (vecchia purge hardcoded @/@home/@games dopo try/finally, irraggiungibile).
- Python: rimosso except ValueError duplicato in format_snapshot_name; bare except: -> except Exception:.
- Validazione path in edit settings (Rust + Python): avvisa "WARNING: path does not exist" se la dir non esiste.

### Warning clippy (tutti risolti - ZERO residui)
- 20 fix automatici via `cargo clippy --fix` (let-chains edition 2024, or_default, if collassabili, next_back al posto di Iterator::last, array al posto di vec!, ecc.).
- 4 manuali: `map_while(Result::ok)` al posto di lines().flatten() nei thread stdout/stderr (no loop-forever), strip_prefix('@') al posto di &s[1..], type alias SnapshotData per "very complex type".

## Versioni Precedenti
- v2.6 (2026-04-12): fix catch-all verify, messaggi status, parser ANSI, parità verify Python/Rust
- v2.5: Interfaccia adattiva, colonne dinamiche, rinomina file
- v2.2: Fix timestamp, .BROKEN conflicts, comando B, logica dinamica
