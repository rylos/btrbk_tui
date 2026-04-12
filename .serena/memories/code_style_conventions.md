# Code Style - BTRBK TUI v2.5

## Python CLI (`btrbk_tui.py`)
- Shebang: `#!/usr/bin/python`
- No classi, solo funzioni e variabili globali
- Naming: `snake_case`
- Imports: `os`, `subprocess`, `datetime`

## Python TUI Pro (`btrbk_tui_pro.py`)
- Shebang: `#!/usr/bin/env python3`
- Docstring modulo in testa
- Naming: `snake_case` funzioni/variabili, `PascalCase` classi, `UPPER_CASE` costanti
- Type hints: `Dict, List, Optional, Tuple` da `typing`
- Classi: `Config`, `SnapshotManager`, `TUIApp`
- Costanti: `CONFIG_FILE` (Path), `DEFAULT_CONFIG` (dict)
- Imports: stdlib prima, nessuna dipendenza esterna (curses è stdlib)
- Error handling: try/except con fallback silenzioso

## Rust (`btrbk_tui_rust/src/main.rs`)
- Edition 2024
- Naming: `snake_case` funzioni, `PascalCase` struct
- Struct: `Config` (Serialize/Deserialize/Clone), `App`
- Error handling: `Result<T, E>`, `if let Ok(...)`, match con fallback
- Helper globali: `run_command()`, `get_max_yx()`
- Nessun `unwrap()` non gestito su operazioni critiche
