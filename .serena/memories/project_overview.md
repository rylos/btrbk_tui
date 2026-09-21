# BTRBK TUI v2.8 - Project Overview

## Scopo
Set completo di strumenti per il ripristino di snapshot Btrfs creati con btrbk. Tre implementazioni con interfacce diverse, configurazione condivisa e parità di funzionalità.

### Versioni Disponibili
- **`btrbk_tui.py`** - CLI semplice con menu numerato
- **`btrbk_tui_pro.py`** - TUI professionale Python con curses
- **`btrbk_tui_rust/`** - TUI Rust ad alte prestazioni (identica alla Pro)

### Comandi TUI (Schermata Principale)
- **↑↓**: Navigazione snapshot | **←→**: Cambio colonna
- **ENTER**: Seleziona e ripristina | **S**: Settings | **R**: Refresh
- **I**: Crea snapshot (`btrbk run --progress`) | **P**: Purge OLD | **B**: Clean BROKEN
- **H**: Reboot (dopo restore) | **Q**: Esci

### Comandi TUI (Settings)
- **↑↓**: Naviga | **ENTER**: Modifica stringa | **SPACE**: Toggle booleano
- **S**: Salva manuale | **ESC**: Torna a main

### Funzionalità Chiave
- Rilevamento dinamico qualsiasi @prefix
- Colonne adattive, ordinamento @ primo poi alfabetico
- Config condivisa `~/.config/btrbk_tui/config.json`
- Restore sicuro: mv → snapshot → verify → rollback se fallisce
- Backup `.BROKEN.TIMESTAMP` unici
- Messaggi status specifici per ogni operazione
- Purge chain-aware (v2.7, solo TUI Python e Rust): interroga il target ssh di btrbk e non cancella mai il parent snapshot del prossimo send incrementale; fail-safe se il target è irraggiungibile (vedi `mem:latest_changes`)
- Desktop entry `btrbk-tui.desktop` (pkexec sulla TUI Rust)
- Repo pubblico su GitHub: niente dati reali di rete/host nel codice o nei test

- `--purge-plan` (dry run della purge) in entrambe le TUI; Home/End; scroll delle colonne
- Uso quotidiano: `sudo btrbk_tui` = TUI Rust (symlink /usr/local/bin)

### Stato: Tutte le versioni ✅ Produzione v2.8 (2026-09-21)

### Documentazione esterna
Nota wiki Joplin dedicata (notebook Backup & Storage): «BTRBK TUI — restore, purge e snapshot btrbk da terminale», id `5e37ad8b6c5542138e3d94ce6903de3c`. Va aggiornata quando cambiano tasti, opzioni da shell o il flusso di restore/purge (poi `wiki log` e `wiki brief --write`).
