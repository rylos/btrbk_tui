# BTRBK TUI

A comprehensive set of tools for restoring Btrfs snapshots created with btrbk, available in Python and Rust with different user interfaces.

## Description

This project provides tools to easily restore Btrfs subvolume snapshots created by the btrbk tool. The tools allow you to:

- **Automatic detection** of all snapshot types present
- **Dynamic interface** that adapts to the number of groups found
- View available snapshots for all subvolumes
- Select and restore specific snapshots
- Automatically manage backup of existing subvolumes
- Persistent configuration shared between versions
- Intelligent cleanup of old snapshots
- System reboot with visual indicators
- Optionally reboot the system after restoration

## ✨ Features v2.8 - TUI Audit (Rust + Python)

A full review of the Rust TUI, the version used day to day, then ported line for
line to the Python TUI so the two stay identical. Every fix below was exercised
for real, on both versions: restore, rollback and `.BROKEN` cleanup on a throw-away
loopback btrfs, snapshot creation and cancelling against a stand-in `btrbk`, the
purge plan against the live backup target.

### 🛡️ **Safety:**
- **`@root` is no longer restored over `@`**: the subvolume to replace was derived from a *type name*, and the type of `@` was spelled `root` — so a snapshot of a subvolume really called `@root` would have replaced the root filesystem. The snapshot prefix is now used as-is. The CLI was already correct
- **Cancelling a btrbk run no longer leaves orphans**: ESC used to `SIGKILL` btrbk alone, leaving `btrfs send`, `ssh` and `pv` running as root with nobody to clean up. btrbk now runs in its own session; ESC sends `SIGINT` to the whole group — what Ctrl-C does in a shell, the case btrbk handles — then `SIGKILL` after 5 seconds to whatever is left
- **`rename(2)` instead of `mv`** for the `.BROKEN` move and its rollback: across filesystems it fails, where `mv` would start copying a subvolume
- **The config never falls back to `/tmp`**: a root tool must not read paths for `btrfs subvolume delete` from a world-writable directory
- **A panic restores the terminal** before printing, instead of leaving it in curses mode

### 🐛 **Bugs:**
- **Busy messages were never drawn**: "Checking backup target...", "Restoring snapshot..." were stored and then `refresh()`ed without being painted, so the interface just froze. They are now drawn before the blocking call
- **With no snapshots, every key was ignored** — including `S`, the one needed to fix a wrong path, and `I`
- **The selection could leave the screen**: with more snapshots than rows the cursor moved on invisibly. The selected column now scrolls and shows `[5-7 of 12]`; the selection is clamped after refresh, purge and restore. `Home`/`End` added
- **Progress meters were not live**: output was split on `\n` only, so `\r`-rewritten progress arrived as one giant line at the end. Streams are now split on both, and a progress line replaces the previous one
- **The "press any key" result screen was usually skipped** because of a race between the pipes closing and the exit status being noticed
- **ESC took a full second** to register (`ESCDELAY`)
- **Purge reports failures** ("3 could NOT be deleted") instead of counting only successes, and names the subvolumes it skipped because they share nothing with the target
- **`ssh_identity`, `ssh_user` and `ssh_port`** from `btrbk.conf` are honoured when querying the target; IPv6 targets and `target send-receive ssh://...` are parsed
- **Timestamps**: btrbk's `short` and `long-iso` formats and the `_N` suffix are understood; a subvolume name containing a dot no longer splits in the wrong place
- A config file missing a field no longer discards the whole file; a failed config save is reported; the header showed `v2.6`

### ✨ **Improvements:**
- **Purge shows its hand first**: the target is checked, then the dialog asks "Delete 18 old snapshots?" — no more confirming blind. `sudo btrbk_tui --purge-plan` prints the same plan without deleting anything
- **Errors say why**: the last line of stderr reaches the status bar ("restore failed, rolled back: restored root has no /etc/fstab")
- **No more flicker**: `erase()` instead of `clear()`, which forced a full terminal repaint ten times a second
- **Any terminal size**: all drawing goes through one clipping helper, so a tiny window can no longer panic or wrap
- Status messages last a fixed time instead of a number of frames; the restore dialog names the snapshot; the footer follows the screen
- **13 unit tests per version** (Rust had 3, Python none): purge planning, snapshot grouping, timestamps, ssh target parsing, output cleaning, progress handling. `cargo test` and `python3 -m unittest discover -s tests -p '*_test.py'` check the same cases, so the two versions cannot drift apart on what a purge may delete
- **Python only**: reading btrbk's output blocked the interface (ESC was ignored until the next line arrived), and after a snapshot run the main loop lost its timeout, freezing status messages until a key was pressed. Output is now read without blocking, like the Rust version does with threads

## ✨ Features v2.7 - Chain-Aware Purge

### 🔗 **Purge no longer breaks incremental backups (Python + Rust):**
- **Root cause**: the purge kept only the most recent snapshot per type, deleting the one the backup target still needed as parent. btrbk does not protect parent snapshots itself (`btrbk.conf(5)`), so the next run had no common parent and fell back to a **full send** — on a ~900 GiB subvolume, over an hour of transfer
- **Fix**: the target is queried over ssh (`btrfs subvolume list -u -R`) and each local snapshot's `UUID` is matched against the target's `received_uuid`. The newest snapshot present on both survives, together with everything newer
- **Fails safe**: if the target is unreachable, or shares no snapshot with the local pool, **nothing is deleted** and the UI says so — a broken chain is never made worse
- **UI feedback**: the status bar announces the target check before the ssh call blocks the interface
- **Unit tests** (Rust) cover the parsing of `btrbk.conf`, of `received_uuid` lists and of `btrfs subvolume show` — including the trap where `Parent UUID:` is matched instead of `UUID:`

### 🧹 **Lint rules pinned in the repository:**
- **`.ruff.toml` added**: the project had no ruff configuration, so the effective rule set was whatever the installed ruff defaulted to. The "zero linter warnings" claim of v2.6 quietly expired as ruff was upgraded — 51 warnings had accumulated by v2.7
- **Explicit rule selection** (correctness, modernisation, bugbear, bandit, pylint) with every waiver documented inline: `subprocess.run` without `check=` is deliberate where return codes drive the rollback logic, broad `except Exception` keeps a fullscreen curses app from dying, naive datetimes match btrbk's own local-time snapshot names
- **166 findings fixed**: modern type annotations (`dict`/`list` over `typing`), `sys.exit()` over the `site` builtin `exit()`, `.values()` iteration, `contextlib.suppress`, unused unpacked variables
- `ruff check` and `cargo clippy` both pass clean, and now stay that way across tool upgrades

## ✨ Features v2.6 - Audit Hardening

### 🛡️ **Safe Restore (all three versions):**
- **Verified rollback**: every rollback command (`mv` / `btrfs delete`) return code is checked. Three distinct outcomes are reported: success, *failed* (rollback succeeded, previous state restored) and *rollback failed* (inconsistent state → `.BROKEN` kept and a CRITICAL message points to it for manual recovery)
- **Source pre-check**: the source snapshot existence is verified **before** touching the current subvolume
- **Subvolume guard**: `btrfs subvolume show` is run before the destructive `mv`, preventing a plain directory from being moved by mistake
- **CLI brought to parity**: `btrbk_tui.py` now performs the same verify + rollback as the TUI versions, reads the shared config, requires root and runs `sync` before reboot

### 🐛 **Robustness fixes:**
- **Rust UTF-8 safety**: all string truncation is char-aware (`truncate_str`), eliminating panics on multibyte characters in snapshot names or btrbk output
- **Snapshot cache**: the snapshots directory is no longer re-read on every frame; the cache is invalidated only on refresh/restore/purge/clean/create. The `R` key now actually refreshes
- **Cleaner output**: the snapshot-creation output area is fully redrawn (no scroll glitches), and dead/unreachable code was removed
- **Zero linter warnings**: `cargo clippy` (Rust) and `ruff check` (Python) both pass clean

## ✨ Features v2.5 - Bug Fixes & Improvements

### 🔄 **Automatic Detection:**
- **No longer limited** to 3 fixed types (@, @home, @games)
- **Automatically scans** the snapshots directory
- **Detects any prefix** (@, @home, @games, @custom, @backup, @work, etc.)
- **Automatically adapts** to any user's btrbk configuration

### 🐛 **Critical Bug Fixes:**
- **Fixed timestamp parsing** - Now supports both `YYYYMMDDTHHMMSS` and `YYYYMMDD_HHMMSS` formats
- **Fixed .BROKEN conflicts** - Unique timestamps prevent restore failures
- **Fixed hardcoded restore logic** - Now fully dynamic for all subvolume types
- **Fixed purge function** - Dynamic detection instead of hardcoded types
- **Simplified log display** - Removed problematic side borders

### 📊 **Adaptive Interface:**
- **Dynamic columns**: Number of columns adapts to groups found
- **Automatic width**: Columns resize automatically
- **Smart sorting**: @ always first, then alphabetical order
- **Snapshot count**: Shows number of snapshots per group

### 🎯 **Supported Configuration Examples:**
```
Basic User:     @ | @home
Gaming User:    @ | @home | @games  
Pro User:       @ | @home | @games | @work | @backup
Server User:    @ | @home | @var | @opt | @srv | @data
```

### 🎨 **Enhanced Interface:**
- **Separator lines** at full screen width
- **Perfect visual consistency** between header and footer
- **Optimized colors** for better readability

### 🎯 **Supported Configuration Examples:**
```
Basic User:     @ | @home
Gaming User:    @ | @home | @games  
Pro User:       @ | @home | @games | @work | @backup
Server User:    @ | @home | @var | @opt | @srv | @data
```

### 🎨 **Enhanced Interface:**
- **Separator lines** at full screen width
- **Perfect visual consistency** between header and footer
- **Optimized colors** for better readability

## Available Versions

### Python
- **`btrbk_tui.py`** - Simple CLI version with text menu
- **`btrbk_tui_pro.py`** - Professional TUI interface with persistent configuration and dynamic columns

### Rust
- **`btrbk_tui_rust/`** - High-performance TUI version written in Rust with ncurses (identical to Python Pro version)

## Prerequisites

### For Python versions:
```bash
# Basic CLI version
python3

# Professional TUI version
python3 (with curses module included)
```

### For Rust version:
```bash
# Rust installation (edition 2024)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Build the project
cd btrbk_tui_rust
cargo build --release
```

## Configuration

The tool assumes by default:
- **Btrfs Pool**: `/mnt/btr_pool`
- **Snapshots directory**: `/mnt/btr_pool/btrbk_snapshots`

**Shared Configuration**: The TUI Pro (Python) and Rust versions share the same JSON configuration file at `~/.config/btrbk_tui/config.json`, ensuring a completely consistent user experience.

## Usage

### Python CLI Version
```bash
sudo ./btrbk_tui.py
```

### Python Professional TUI Version
```bash
sudo ./btrbk_tui_pro.py
```

### Rust TUI Version (identical to Python Pro)
```bash
cd btrbk_tui_rust
sudo ./target/release/btrbk_tui
```

## Features

### CLI Version (`btrbk_tui.py`)
- **Numbered list** of all snapshots organized by type
- **Number-based selection** with simple interface
- **Complete dynamic support** for any configuration (@, @home, @games, @custom, @backup, etc.)
- **Simple interface** for occasional use
- **Automatic management** of .BROKEN backups
- **Automatic detection** of all snapshot types present

### Professional TUI Version (`btrbk_tui_pro.py`)
- **Dynamic interface**: Columns that automatically adapt to groups found
- **Persistent configuration**: Automatic saving to `~/.config/btrbk_tui/config.json`
- **Advanced navigation**: Arrow keys for fluid navigation
- **Complete settings screen**: `S` key for advanced configuration
- **Configurable settings**: Directories, auto-cleanup, confirmations, timestamps
- **Status messages**: Real-time operation feedback
- **Themes and colors**: Professional interface with highlighting
- **Auto-save**: Every change is automatically saved
- **Snapshot creation**: Dedicated interface for `btrbk run --progress`
- **Smart purge**: Automatic cleanup of old snapshots
- **Reboot system**: Visual indicators and dedicated shortcuts

### Rust TUI Version (`btrbk_tui_rust/`)
- **Optimized performance**: Native Rust implementation
- **Identical interface**: Layout and functionality identical to Python Pro version
- **Shared configuration**: Uses exactly the same JSON file as Python version
- **Complete settings screen**: Same editing functionality as Python version
- **Efficient memory management**: Ideal for resource-limited systems
- **Total compatibility**: Zero functional differences with Python Pro version
- **Optimized compilation**: Rust edition 2024, zero errors and warnings
- **Snapshot creation**: Multi-threaded interface for real-time output
- **Purge and reboot**: All advanced features implemented

## Supported Snapshot Structure

The tool automatically handles snapshots with this nomenclature:
- `@.YYYYMMDD_HHMMSS` - Root subvolume snapshot
- `@home.YYYYMMDD_HHMMSS` - Home subvolume snapshot
- `@games.YYYYMMDD_HHMMSS` - Games subvolume snapshot
- `@custom.YYYYMMDD_HHMMSS` - Custom subvolume snapshots
- `@backup.YYYYMMDD_HHMMSS` - Backup snapshots
- `@work.YYYYMMDD_HHMMSS` - Work snapshots
- **And any other prefix** that starts with `@` followed by a dot

**The tool automatically adapts** to any user's btrbk configuration!

## TUI Controls

### Dynamic Column Versions (TUI Pro Python/Rust):

#### Main Screen:
- **↑↓**: Vertical navigation through snapshots, **Home/End** for first and last
- **←→**: Dynamic column switching (adaptive to number of groups)
- **ENTER**: Snapshot selection and restoration
- **S**: Access settings screen
- **R**: Refresh snapshot list
- **I**: Create new snapshots (btrbk run --progress)
- **P**: Purge old snapshots (keeps what the backup target still needs)
- **H**: System reboot (when needed)
- **Q**: Exit application

#### Settings Screen:
- **↑↓**: Navigate between options
- **ENTER**: Edit value (for strings)
- **SPACE**: Toggle value (for booleans)
- **S**: Manual save (optional, auto-save active)
- **ESC**: Return to main screen

### Advanced Features:

#### Instant Snapshot Creation (I Key):
- **Executes**: `btrbk run --progress` with dedicated interface
- **Real-time output**: Professional progress visualization
- **Dedicated window**: Fullscreen with borders and title
- **Cancellation**: ESC to interrupt operation at any time
- **Auto-scroll**: Automatic scrolling for long output
- **Complete feedback**: Colored success/error messages
- **Stderr handling**: Perfectly aligned output without overlaps

#### Smart Purge (P Key):
- **Analyzes** all snapshots by type (@, @home, @games)
- **Chain-aware**: queries the backup target and keeps the newest snapshot the target also holds, plus everything newer. That snapshot is the parent for the next incremental send — deleting it forces a full send on the next run
- **Deletes** only the snapshots older than that parent
- **Refuses to purge** when the target is unreachable, or when no snapshot is shared with it: without that information there is no safe way to tell what can go
- **Confirmation** after the check, stating how many snapshots will go
- **Dry run**: `sudo btrbk_tui --purge-plan` (or `sudo ./btrbk_tui_pro.py --purge-plan`) lists what would be deleted
- **Detailed feedback** on how many snapshots were deleted, failed or skipped
- **Error handling**: Continues operation even if individual deletions fail
- **Space optimization**: Frees disk space without ever breaking the incremental chain

> **Why this matters:** btrbk does not protect snapshots that are still needed as
> parents for incremental backups (see `btrbk.conf(5)`). A purge that keeps only
> the most recent snapshot per type will silently break the chain whenever the
> target is behind — for example after a backup run was interrupted. The next run
> then has no common parent and falls back to a full send, which on a large
> subvolume means hours of transfer.

#### Smart Reboot:
- **R Key**: Always available for snapshot list refresh
- **H Key**: Appears in footer after restore for quick reboot
- **Persistent warning**: Status bar shows "⚠ REBOOT REQUIRED" after each restore
- **Dedicated keys**: R for refresh, H for reboot, I for snapshot, P for purge - no confusion
- **Visual indicators**: Dynamic footer that changes based on context

## Desktop File

`btrbk-tui.desktop` launches the Rust TUI through `pkexec`, so the desktop's
polkit agent asks for the password instead of the tool failing on a missing
root. It expects the binary at `/usr/local/bin/btrbk_tui`; adjust `Exec=` if you
installed it elsewhere.

```bash
install -Dm644 btrbk-tui.desktop ~/.local/share/applications/btrbk-tui.desktop
update-desktop-database ~/.local/share/applications
```

## Security

⚠️ **WARNING**: These tools require root privileges and modify system subvolumes. Use with caution and always after verifying the presence of valid backups.

### Implemented Security Measures:
- **Mandatory confirmations**: Confirmation dialogs for all critical operations
- **Automatic backup**: Existing subvolumes are renamed to .BROKEN before restoration
- **Source pre-check & subvolume guard**: the source snapshot must exist and the current subvolume must be a valid btrfs subvolume before any destructive operation
- **Verified rollback**: on failure the original subvolume is restored and the rollback outcome is checked; if the rollback itself fails, the `.BROKEN` backup is kept and a CRITICAL message indicates manual recovery is needed
- **Error handling**: Robust operations with fallback and clear error messages
- **Optional auto-cleanup**: Configurable automatic cleanup of .BROKEN files

## Compatibility

- **Operating System**: Linux with Btrfs filesystem
- **Dependencies**: btrfs-progs, btrbk
- **Desktop**: Tested on KDE Plasma, compatible with other DEs
- **Supported subvolumes**: Any configuration starting with @ (dynamic)
- **Architectures**: x86_64, ARM64 (Rust), all architectures supported by Python

## Advanced Configuration

Both TUI versions (Python Pro and Rust) share the configuration saved at:
```
~/.config/btrbk_tui/config.json
```

### Configurable settings:
- **btr_pool_dir**: Btrfs pool directory (default: `/mnt/btr_pool`)
- **snapshots_dir**: Snapshots directory (default: `/mnt/btr_pool/btrbk_snapshots`)
- **auto_cleanup**: Auto-cleanup of .BROKEN files (default: `false`)
- **confirm_actions**: Action confirmation (default: `true`)
- **show_timestamps**: Display formatted timestamps (default: `true`)
- **theme**: Interface theme (default: `"default"`)

### Example configuration file:
```json
{
  "btr_pool_dir": "/mnt/btr_pool",
  "snapshots_dir": "/mnt/btr_pool/btrbk_snapshots",
  "auto_cleanup": false,
  "confirm_actions": true,
  "show_timestamps": true,
  "theme": "default"
}
```

### Configuration Management:
- **Automatic loading**: At startup of any TUI version
- **Automatic saving**: On every change in TUI versions
- **Synchronization**: Changes in one version apply immediately to the other
- **Fallback**: If file is corrupted or missing, default values are used

## Which Version to Choose?

### **CLI (`btrbk_tui.py`)**
- ✅ Occasional or sporadic use
- ✅ Automated scripts
- ✅ Resource-limited environments
- ✅ When only basic functionality is needed

### **TUI Pro (`btrbk_tui_pro.py`)**
- ✅ Frequent and interactive use
- ✅ Advanced configuration and customization
- ✅ When Python is preferred for modifications
- ✅ Development and debugging
- ✅ Complete snapshot management
- ✅ Dynamic interface that adapts to any configuration

### **Rust (`btrbk_tui_rust/`)**
- ✅ Maximum performance and speed
- ✅ Systems with limited or absent Python
- ✅ Production environments
- ✅ When memory efficiency is needed
- ✅ All Pro version features
- ✅ Dynamic interface identical to Python version

## Benefits of Complete Alignment

### **Unified Configuration:**
- Single configuration file for both TUI versions
- Automatically synchronized changes
- Consistent user experience

### **Identical Features:**
- Same interface and controls
- Same configuration options
- Same behavior and workflow
- Same advanced features (purge, reboot, settings)

### **Total Flexibility:**
- Switch from Python to Rust without losing configurations
- Choose language based on specific needs
- Simplified maintenance with shared configuration

### **Optimized Performance:**
- Python: Ease of modification and debugging
- Rust: Execution speed and memory efficiency
- Both: Same user experience

## Project Structure

```
btrbk_tui/
├── README.md                      # Complete documentation
├── btrbk_tui.py                  # Simple CLI version
├── btrbk_tui_pro.py              # Python professional TUI version
├── btrbk_tui_rust/           # Rust professional TUI version
│   ├── Cargo.toml               # Rust configuration (edition 2024)
│   ├── src/main.rs              # Rust source code
│   └── target/release/          # Compiled binary
├── btrbk-tui.desktop             # Desktop file for DE integration
└── .git/                         # Git repository
```

## Development and Contributions

### **Languages used:**
- **Python 3**: CLI and TUI Pro versions
- **Rust 2024**: High-performance TUI version
- **JSON**: Shared configuration

### **Dependencies:**
- **Python**: `curses`, `json`, `pathlib`, `subprocess`, `os` modules
- **Rust**: `ncurses`, `serde`, `serde_json`, `chrono`, `dirs`, `libc`

### **Linting:**
- **Python**: [`ruff`](https://docs.astral.sh/ruff/) — `ruff check .`
- **Rust**: `cargo clippy` — both pass with zero warnings
- **Tests**: `cargo test` (in `btrbk_tui_rust/`) and `python3 -m unittest discover -s tests -p '*_test.py'` — the same 13 cases on both sides

### **Testing:**
- Tested on Arch Linux with KDE Plasma 6
- Compatible with other Linux desktop environments
- Full support for Btrfs filesystem

## Typical Usage Workflow

1. **Startup**: `sudo ./btrbk_tui_pro.py` or Rust version
2. **Navigation**: Use arrows to explore available snapshots
3. **Configuration**: Press `S` to modify settings if needed
4. **Snapshot creation**: Use `I` to create new snapshots with btrbk
5. **Selection**: Choose snapshot to restore with `ENTER`
6. **Confirmation**: Confirm the restoration operation
7. **Reboot**: Choose whether to reboot immediately or continue
8. **Cleanup**: Use `P` to delete old snapshots when needed
9. **Quick reboot**: Use `H` to reboot when indicated

## License

Open source project - see source code for implementation details.

## Contributing

Contributions welcome! The project demonstrates implementing the same functionality in different languages (Python/Rust) with interfaces optimized for different use cases, while maintaining full configuration compatibility and identical user experience.

### **Project characteristics:**
- Modular and well-structured architecture
- Shared configuration between different languages
- Professional and intuitive user interfaces
- Robust error handling
- Performance optimized for each language
- Complete and up-to-date documentation
- Advanced snapshot management features
- Integrated security system

## Installation

### Quick Start
```bash
# Clone the repository
git clone https://github.com/rylos/btrbk_tui.git
cd btrbk_tui

# Make scripts executable
chmod +x btrbk_tui.py btrbk_tui_pro.py

# For Rust version
cd btrbk_tui_rust
cargo build --release
cd ..

# Run (requires root privileges)
sudo ./btrbk_tui_pro.py
```

### Requirements Check
```bash
# Verify btrfs tools
which btrfs btrbk

# Verify Python
python3 --version

# Verify Rust (for Rust version)
rustc --version
```

## Screenshots

The dynamic interface automatically adapts to your btrbk configuration:

**2 Groups (Basic):**
```
@ (3) | @HOME (2)
```

**4 Groups (Advanced):**
```
@ (3) | @HOME (2) | @GAMES (4) | @WORK (1)
```

**6+ Groups (Server):**
```
@ (3) | @HOME (2) | @VAR (1) | @OPT (2) | @SRV (1) | @DATA (3)
```

## Support

- **Issues**: Report bugs or request features via GitHub Issues
- **Documentation**: Complete documentation in this README
- **Community**: Contributions and feedback welcome

---

**Made with ❤️ for the Btrfs and btrbk community**
