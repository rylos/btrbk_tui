# BTRBK TUI v2.6 - Project Overview

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

### Stato: Tutte le versioni ✅ Produzione v2.6
