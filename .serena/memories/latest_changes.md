# Latest Changes - BTRBK TUI v2.10

## v2.10 - Hooks after the snapshots change (2026-10-26)

- After a purge that deleted something and after every `btrbk run` started with `I` (successful or not), Rust and Python run the programs in `/etc/btrbk_tui/post-change.d/` in name order, like run-parts. A failed hook is named in the status bar.
- Root hooks only: a regular executable file owned by root, not writable by group/others (a symlink counts as its target), in a directory with the same rules. Deliberately NOT a user config option: the TUI runs as root and a command read from the user's home would be an escalation.
- Tested pure functions: `hook_allowed(uid, mode, is_file)`, `hook_name_allowed(name)`; I/O: `post_change_hooks(dir)`, `App::run_post_change_hooks` / `TUIApp.run_post_change_hooks`. 16 tests per version.
- Use on the author's desktop: `/etc/btrbk_tui/post-change.d/50-btrbk-nas` -> `/usr/local/bin/btrbk-nas-push-uuids` (btrbk-nas project): updates on the NAS the list of the PC's snapshots, from which the DSM app works out the next parent. `I` does not go through btrbk.service, so without the hook the list would fall behind.

## v2.9 - Any btrbk layout (2026-09-24, from GitHub issues #1-#4)

- **Snapshot names without `@`**: `split_snapshot_name` accepts `<name>.<timestamp>` if the timestamp is a valid btrbk timestamp (before, `@` was required, which excluded btrbk's default naming). Excludes `scripts.d`, `prune.sh`, `@home.BROKEN`.
- **User config behind sudo/pkexec**: `config_candidates(invoking_home, home)` -> `<user>/.config/btrbk_tui`, `/root/.config/btrbk_tui`, then the same with `btrbk_restore` (directory name until 2025-09-23, renamed without migration: the old config was silently ignored). The old path is read but not written; saving moves it to the new one, with `chown` to the user (SUDO_UID/PKEXEC_UID, home via getpwuid). `invoking_user()`, `is_legacy_config()`.
- **`--config FILE` / `-c`** in both TUIs (also handy for tests: no more `HOME=` trick).
- **Two-line restore dialog** with the path touched: "Replaces <pool>/<sub> ..." or "WARNING: ... does not exist, it will be CREATED" (symptom of a btr_pool_dir that is not the top level). `confirm_dialog` accepts multi-line messages.
- **Root/home check by mount**: `mounted_subvolume(mountinfo, mountpoint)` reads /proc/self/mountinfo; root checks if the subvolume is "@" or the one mounted on `/`, home checks if "@home" or the one on `/home`.
- **CLI**: imports from `btrbk_tui_pro` (Config, group_snapshots, split_snapshot_name, parse_btrbk_timestamp, mounted_subvolume, VERSION) instead of duplicating the logic: it must run from the repo directory next to `btrbk_tui_pro.py`.
- 15 tests per version. README: "Layout requirements" section (btr_pool_dir = top level subvolid=5; snapshots outside the subvolumes, `/home/.snapshots` cannot be restored by swap).
- GitHub issues: all answered (#1-#5); #2 (running as a non-root user) and #3 (several volumes read from btrbk.conf) stay open as feature requests.
- **First GitHub release**: v2.9.0 (tag on 6f3c6c2) with a stripped x86_64 Rust binary + `.sha256`. #5 closed, #1/#4 labelled `bug` and open awaiting confirmation, #2/#3 `enhancement`.


## v2.8 - Rust TUI audit (2026-09-21)

`btrbk_tui` (symlink /usr/local/bin -> target/release) is the version used every day: main.rs rewritten consistently (~1850 lines, 13 unit tests). Full details in the README, v2.8 section.

### Safety
- **`@root` was restored over `@`** (Rust + Python TUI): the subvolume was derived from a "type" and the type of `@` was "root". Now `restore_snapshot(snapshot, subvol_name)` gets the prefix as-is; verify matches on "@" / "@home". The CLI was already correct.
- ESC during `btrbk run`: btrbk runs in its own session (`setsid` in pre_exec, stdin null); ESC sends SIGINT to the group, SIGKILL after 5 s, and a final SIGKILL to the processes left. Before, only btrbk was killed, leaving orphaned btrfs send/ssh running as root.
- `fs::rename` instead of `mv` (across filesystems it fails instead of copying); check that .BROKEN does not exist already.
- Config fallback `/root`, not `/tmp`; panic hook that calls `endwin()`.

### Bugs
- "busy" messages were never drawn (set_status + refresh without draw) -> `show_busy()` redraws before blocking.
- Without snapshots every key was ignored (S and I too).
- Off-screen selection: now the selected column scrolls (`[5-7 of 12]`), `clamp_selection`, Home/End.
- btrbk output split only on `\n`: progress with `\r` not live. Now `forward_stream` splits on both and `OutputLog` replaces transient lines.
- Race that skipped the "press any key" screen; ESCDELAY at 25 ms; header stuck at v2.6 (now `CARGO_PKG_VERSION`).
- `#[serde(default)]` on Config; failed config saves reported.

### Improvements
- Purge: checks the target first, then asks for confirmation with the real count; reports failed deletions and skipped subvolumes. `sudo btrbk_tui --purge-plan` = dry run (also `--version`, `--help`).
- `parse_ssh_target`: honours ssh_identity/ssh_user/ssh_port from btrbk.conf, IPv6, `target send-receive ssh://`.
- `run_command -> Result<(), String>` (last line of stderr): errors say why. `RestoreOutcome::Failed(String)` / `RollbackFailed(String)`.
- `erase()` instead of `clear()` (no flicker); all drawing goes through `put()`, which clips -> no panic at any terminal size.
- Fixed-duration status (`Instant`, `STATUS_*` constants), `Screen` enum, `key_char()` instead of numeric codes, `Rc<SnapshotGroups>` cache.
- Timestamps: short/long/long-iso formats and `_N` suffix; snapshot name split at the LAST dot (`split_snapshot_name`).
- Outcome messages in ASCII (`[OK]`/`[FAILED]`): under pkexec the locale is C.

### How it was verified (reusable)
- Restore/rollback/clean: btrfs on loopback (truncate + mkfs.btrfs + mount -o loop), config via `sudo env HOME=<dir>`, TUI driven with `tmux send-keys` / `capture-pane`.
- Snapshot creation and ESC: fake `btrbk` at the front of PATH (`sudo env PATH=...`).
- Purge: `--purge-plan` against the real target (read only).

### Python TUI brought to parity (same day)
`btrbk_tui_pro.py` rewritten as a 1:1 port of the Rust version: same module-level pure functions (`clean_output_line`, `split_snapshot_name`, `group_snapshots`, `parse_btrbk_timestamp`, `compute_purge_plan`, `parse_ssh_target`, ...), `OutputLog`, `StreamSplitter`, `put()`, `show_busy`, `clamp_selection`, scrolling, `--purge-plan`/`--version`/`--help`, `os.rename`, `start_new_session=True` + `os.killpg`.
- Python-only bugs fixed: `process.stdout.readline()` blocked the interface (ESC ignored until the next line) -> now non-blocking `select` + `os.read`; after snapshot creation `nodelay(False)` lost the main loop's `timeout(100)` (status messages frozen until a key was pressed).
- `restore_snapshot` returns `(outcome, reason)`; `plan_purge` raises `PurgeError`; `run_command` returns `None` or the reason.
- Tests: `tests/parsing_test.py` (13 cases mirroring the Rust ones). CAUTION: `.gitignore` excludes `test_*`, which is why the file is NOT called `test_parsing.py`; run it with `python3 -m unittest discover -s tests -p '*_test.py'`.
- The CLI (`btrbk_tui.py`) is unchanged apart from the version string: it has no purge nor snapshot creation, and its prefix mapping was already correct.

## v2.7

## v2.7 - Chain-aware purge (2026-08-04, commit 31e4d8f)

### Problem
The purge kept only the newest snapshot per type and deleted the one the backup target used as **parent** for the next incremental send. btrbk does NOT protect parents (btrbk.conf(5)) -> the next run finds no common parent and falls back to a full send (~900 GiB, over an hour). That is how the local chain broke on 2026-08-04.

### Solution (btrbk_tui_pro.py + Rust; the CLI has no purge, untouched)
- The target is read from `/etc/btrbk/btrbk.conf` (constant `BTRBK_CONF`): first ssh target.
- The target is queried over ssh: `btrfs subvolume list -u -R` -> set of `received_uuid`s.
- For each local snapshot the UUID is read (`btrfs subvolume show`) and compared with the target's received_uuids.
- The newest snapshot present on both survives + everything newer; only what precedes it is deleted.
- **Fail-safe**: target unreachable or no snapshot in common -> NOTHING is deleted and the UI says so (an already broken chain is not made worse).
- The status bar announces the target check before the ssh call blocks the interface.
- Rust: parsing helpers as pure functions (`parse_target_url`, `parse_received_uuids`, `parse_subvolume_uuid`) + I/O wrappers (`btrbk_target_url`, `target_received_uuids`, `local_subvolume_uuid`).
- Python: `SnapshotManager._get_target_url`, `get_target_received_uuids`, `get_local_uuid`.
- **Known trap**: a sloppy match on `btrfs subvolume show` returns "Parent UUID:" instead of "UUID:" (covered by a test).

### Automated tests (new)
- `btrbk_tui_rust/src/main.rs` has `mod tests` with 3 unit tests: `target_url_is_the_first_ssh_target`, `received_uuids_skip_unset_ones`, `subvolume_uuid_ignores_parent_and_received`. Run with `cargo test`.
- Commit 3ebb5c4: test samples use **placeholder** hosts/paths/UUIDs (the repo is public: never LAN IPs, NAS paths or real UUIDs in tests or code).

### Ruff lint pinned in the repo (commit 653c054)
- Without a config, the rule set depended on the installed ruff version: 51 warnings accumulated just from tool upgrades.
- `.ruff.toml` pins the selection: E, W, F, I, UP, B, C4, SIM, RET, PL, RUF, S, DTZ, EXE (target py39).
- Exceptions documented inline: PLW1510 (return codes checked by hand in the restore/rollback path), BLE001 (broad catches so the curses terminal is not left unusable), S110, DTZ005/DTZ007 (btrbk snapshot names in local time), PLW0603.
- 166 findings fixed with no behaviour change: builtin generics instead of `typing`, `sys.exit()`, `.values()`, `contextlib.suppress`, unused unpacked variables.

### Desktop entry (commit d952dc5)
- `btrbk-tui.desktop` was a broken symlink into the author's home; now it is a real file: `Exec=pkexec /usr/local/bin/btrbk_tui`, `Terminal=true`. Passes `desktop-file-validate`. Installation documented in the README (`install -Dm644 ... ~/.local/share/applications/`).

## Earlier Versions
- v2.6 (2026-06-24): hardening audit - verified rollback with 3 outcomes (success/failed/rollback_failed), source pre-check, `btrfs subvolume show` guard, CLI at parity (verify+rollback, shared config, root check, sync), UTF-8 safe `truncate_str`, `render_output_area`, snapshot cache (invalidated on R/restore/purge/clean/create/edit path), zero clippy warnings, ruff hook
- v2.6 (2026-04-12): catch-all verify fix, status messages, ANSI parser, verify parity Python/Rust
- v2.5: Adaptive interface, dynamic columns, file rename
- v2.2: Timestamp fix, .BROKEN conflicts, B command, dynamic logic
