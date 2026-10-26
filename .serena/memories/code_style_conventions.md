# Code Style - BTRBK TUI v2.10

## Python (common)
- Lint: `ruff` with rules pinned in `.ruff.toml` (E, W, F, I, UP, B, C4, SIM, RET, PL, RUF, S, DTZ, EXE; target py39). Exceptions documented inline (PLW1510, BLE001, S110, DTZ005/DTZ007, PLW0603)
- Type hints with builtin generics (`dict[str, list[str]]`, `set[str]`, `tuple[...]`) and optionals as `str | None` (evaluated at runtime, without `from __future__ import annotations` -> needs Python 3.10+), NO longer `typing.Dict/List/Optional/Tuple`
- `sys.exit()`, not the builtin `exit()`; `contextlib.suppress` instead of try/except/pass
- `subprocess.run()` without `check=`: return codes are checked by hand (needed by the rollback logic)
- Sorted imports (isort via ruff), stdlib only

## Python CLI (`btrbk_tui.py`)
- Shebang: `#!/usr/bin/python`
- No classes, only functions and global variables
- Naming: `snake_case`
- Imports: `json`, `os`, `subprocess`, `sys`, `datetime`, `pathlib`

## Python TUI Pro (`btrbk_tui_pro.py`)
- Shebang: `#!/usr/bin/env python3`
- Module docstring at the top
- Naming: `snake_case` functions/variables, `PascalCase` classes, `UPPER_CASE` constants
- Classes: `Config`, `SnapshotManager`, `TUIApp`
- Constants: `CONFIG_FILE` (Path), `BTRBK_CONF`, `DEFAULT_CONFIG` (dict)
- Error handling: broad try/except with a safe fallback (fullscreen curses app: an unhandled exception leaves the terminal unusable)

## Rust (`btrbk_tui_rust/src/main.rs`)
- Edition 2024 (let-chains allowed)
- Naming: `snake_case` functions, `PascalCase` structs/enums
- Structs: `Config` (Serialize/Deserialize/Clone), `App`
- Error handling: `Result<T, E>`, `if let Ok(...)`, match with fallback
- No unhandled `unwrap()` on critical operations
- String truncation always with `truncate_str` (never byte slicing `&s[..n]`)
- Parsing of external output: pure functions kept apart from the I/O wrappers, covered by unit tests in `mod tests`
- `cargo clippy --release` must stay at zero warnings
