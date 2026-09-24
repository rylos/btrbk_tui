# Code Style - BTRBK TUI v2.9

## Python (comune)
- Lint: `ruff` con regole fissate in `.ruff.toml` (E, W, F, I, UP, B, C4, SIM, RET, PL, RUF, S, DTZ, EXE; target py39). Deroghe documentate inline (PLW1510, BLE001, S110, DTZ005/DTZ007, PLW0603)
- Type hints con generics builtin (`dict[str, list[str]]`, `set[str]`, `tuple[...]`) e opzionali come `str | None` (valutati a runtime, senza `from __future__ import annotations` -> serve Python 3.10+), NON più `typing.Dict/List/Optional/Tuple`
- `sys.exit()` e non il builtin `exit()`; `contextlib.suppress` al posto di try/except/pass
- `subprocess.run()` senza `check=`: i returncode si controllano a mano (necessario per la logica di rollback)
- Imports ordinati (isort via ruff), solo stdlib

## Python CLI (`btrbk_tui.py`)
- Shebang: `#!/usr/bin/python`
- No classi, solo funzioni e variabili globali
- Naming: `snake_case`
- Imports: `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`

## Python TUI Pro (`btrbk_tui_pro.py`)
- Shebang: `#!/usr/bin/env python3`
- Docstring modulo in testa
- Naming: `snake_case` funzioni/variabili, `PascalCase` classi, `UPPER_CASE` costanti
- Classi: `Config`, `SnapshotManager`, `TUIApp`
- Costanti: `CONFIG_FILE` (Path), `BTRBK_CONF`, `DEFAULT_CONFIG` (dict)
- Error handling: try/except ampi con fallback sicuro (app curses fullscreen: un'eccezione non gestita lascia il terminale inutilizzabile)

## Rust (`btrbk_tui_rust/src/main.rs`)
- Edition 2024 (let-chains ammesse)
- Naming: `snake_case` funzioni, `PascalCase` struct/enum
- Struct: `Config` (Serialize/Deserialize/Clone), `App`
- Error handling: `Result<T, E>`, `if let Ok(...)`, match con fallback
- Nessun `unwrap()` non gestito su operazioni critiche
- Troncamento stringhe sempre con `truncate_str` (mai slicing per byte `&s[..n]`)
- Parsing di output esterni: funzioni pure separate dai wrapper I/O, coperte da unit test in `mod tests`
- `cargo clippy --release` deve restare a zero warning
