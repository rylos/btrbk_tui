#!/usr/bin/env python3
"""
BTRBK Restore Tool - Professional TUI Version
A professional terminal user interface for restoring Btrfs snapshots created with btrbk.
"""

import contextlib
import curses
import json
import locale
import os
import pwd
import re
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VERSION = "2.9.0"

# btrbk configuration, read to discover the backup target
BTRBK_CONF = "/etc/btrbk/btrbk.conf"

# Default configuration
DEFAULT_CONFIG = {
    "btr_pool_dir": "/mnt/btr_pool",
    "snapshots_dir": "/mnt/btr_pool/btrbk_snapshots",
    "auto_cleanup": False,
    "confirm_actions": True,
    "show_timestamps": True,
    "theme": "default"
}

# Settings screen entries, in display order
SETTINGS = [
    ("BTR Pool Directory", "btr_pool_dir"),
    ("Snapshots Directory", "snapshots_dir"),
    ("Auto Cleanup .BROKEN", "auto_cleanup"),
    ("Confirm Actions", "confirm_actions"),
    ("Show Timestamps", "show_timestamps"),
]

KEY_ESC = 27
ENTER_KEYS = (curses.KEY_ENTER, 10, 13)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)

# How long status messages stay on screen, in seconds
STATUS_SHORT = 3
STATUS_MEDIUM = 5
STATUS_LONG = 10
STATUS_RESULT = 15
STATUS_CRITICAL = 60

# Lines of btrbk output kept in memory while creating snapshots
MAX_OUTPUT_LINES = 1000

ANSI_SEQUENCE = re.compile(r"\x1b\[[^A-Za-z~]*[A-Za-z~]?|\x1b")


# --- pure helpers (covered by tests/parsing_test.py) -------------------------

def clean_output_line(line: str) -> str:
    """Strip ANSI escape sequences and control characters from a line of output."""
    line = ANSI_SEQUENCE.sub("", line).replace("\t", " ")
    return "".join(c for c in line if c.isprintable())


def split_snapshot_name(name: str) -> tuple[str, str] | None:
    """Split "@home.20260803T0000" or "home.20260803T0000" into subvolume name and timestamp.

    The timestamp never contains a dot, the subvolume name might. A name
    counts as a snapshot only when what follows the last dot is a btrbk
    timestamp: btrbk names snapshots after the subvolume, which may or may
    not start with "@".
    """
    prefix, dot, timestamp = name.rpartition(".")
    if not dot or not prefix or parse_btrbk_timestamp(timestamp) is None:
        return None
    return prefix, timestamp


def group_snapshots(names) -> list[tuple[str, list[str]]]:
    """Group snapshot names by subvolume.

    "@" comes first, then alphabetically; inside a group the newest snapshot is
    first. Names that are not snapshots are dropped.
    """
    groups: dict[str, list[str]] = {}
    for name in names:
        parts = split_snapshot_name(name)
        if parts:
            groups.setdefault(parts[0], []).append(name)
    # "@" is a prefix of every other name, so plain ordering puts it first
    return [(prefix, sorted(groups[prefix], reverse=True)) for prefix in sorted(groups)]


def parse_btrbk_timestamp(timestamp: str) -> datetime | None:
    """Parse the timestamp btrbk puts in snapshot names.

    Understands every timestamp_format flavour (short, long, long-iso) and the
    "_N" suffix btrbk adds when a name is already taken.
    """
    # legacy "YYYYMMDD_HHMMSS" names: here the underscore is not a "_N" suffix
    with contextlib.suppress(ValueError):
        return datetime.strptime(timestamp, "%Y%m%d_%H%M%S")

    # drop the "_N" suffix, then the UTC offset long-iso carries: the name
    # already is local time
    base = re.split(r"[_+-]", timestamp, maxsplit=1)[0]

    formats = {8: "%Y%m%d", 13: "%Y%m%dT%H%M", 15: "%Y%m%dT%H%M%S"}
    fmt = formats.get(len(base))
    if fmt is None:
        return None
    try:
        return datetime.strptime(base, fmt)
    except ValueError:
        return None


def config_candidates(invoking_home: Path | None, home: Path | None) -> list[Path]:
    """Where the configuration may live, most wanted first.

    Under sudo or pkexec it belongs to the user who invoked the tool, not to
    root: that is where they create it, since sudo resets HOME to /root.
    "btrbk_restore" is the name the directory had until 2025-09: it is still
    read, never written.
    """
    homes = [invoking_home] if invoking_home else []
    if home and home not in homes:
        homes.append(home)
    # Never fall back on a world-writable directory: this tool runs as root
    # and the config decides which paths rename and btrfs delete act on
    if not homes:
        homes.append(Path("/root"))
    return [h / ".config" / d / "config.json" for d in ("btrbk_tui", "btrbk_restore") for h in homes]


def is_legacy_config(path: Path) -> bool:
    return path.parent.name == "btrbk_restore"


@dataclass
class InvokingUser:
    """The user behind sudo or pkexec."""

    uid: int
    gid: int
    home: Path


def invoking_user() -> InvokingUser | None:
    try:
        uid = int(os.environ.get("SUDO_UID") or os.environ["PKEXEC_UID"])
        entry = pwd.getpwuid(uid)
    except (KeyError, ValueError):
        return None
    if uid == 0:
        return None
    return InvokingUser(uid=uid, gid=entry.pw_gid, home=Path(entry.pw_dir))


def mounted_subvolume(mountinfo: str, mountpoint: str) -> str | None:
    """Subvolume mounted at `mountpoint`, relative to the top level of its filesystem.

    Reads the content of /proc/self/mountinfo.
    """
    found = None
    for line in mountinfo.splitlines():
        mount, sep, fs = line.partition(" - ")
        fields = mount.split()
        # the last mount on a path is the one in effect
        if sep and fs.split()[:1] == ["btrfs"] and len(fields) > 4 and fields[4] == mountpoint:
            found = fields[3].lstrip("/")
    return found


@dataclass
class PurgePlan:
    """What a purge would delete, computed before touching anything."""

    delete: list[str] = field(default_factory=list)   # snapshot names, oldest first
    skipped: list[str] = field(default_factory=list)  # subvolumes sharing nothing with the target


def compute_purge_plan(groups, target_uuids: set[str]) -> PurgePlan:
    """Decide what a purge deletes.

    `groups` lists, per subvolume, its snapshots oldest first as (name, uuid),
    uuid being None when it could not be read.

    The newest snapshot the target also holds is the parent for the next
    incremental send: it survives, together with everything newer. A subvolume
    with nothing in common with the target is skipped altogether — its chain is
    already broken, deleting more would only force a bigger full send.
    """
    plan = PurgePlan()
    for prefix, snapshots in groups:
        if len(snapshots) <= 1:
            continue
        keep_from = None
        for index, (_, uuid) in enumerate(snapshots):
            if uuid and uuid in target_uuids:
                keep_from = index
        if keep_from is None:
            plan.skipped.append(prefix)
        else:
            plan.delete.extend(name for name, _ in snapshots[:keep_from])
    return plan


@dataclass
class SshTarget:
    """Where btrbk sends its backups, as far as ssh is concerned."""

    host: str
    path: str
    port: str | None = None
    user: str | None = None
    identity: str | None = None


def parse_ssh_target(conf: str) -> SshTarget | None:
    """First ssh:// target of a btrbk configuration, with the ssh options in effect there."""
    options: dict[str, str | None] = {"ssh_user": None, "ssh_identity": None, "ssh_port": None}

    for line in conf.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1]
        if key in options:
            # "no" and "default" are how btrbk.conf spells "unset"
            options[key] = None if value in {"no", "default"} else value
            continue
        if key != "target":
            continue

        # both "target ssh://..." and "target send-receive ssh://..."
        url = value if value.startswith("ssh://") else (parts[2] if len(parts) > 2 else "")
        if not url.startswith("ssh://"):
            continue
        hostport, slash, path = url[len("ssh://"):].partition("/")
        if not slash:
            return None

        # "[::1]:2222" — an IPv6 address has colons of its own
        if hostport.startswith("[") and "]" in hostport:
            host, _, after = hostport[1:].partition("]")
            port = after[1:] if after.startswith(":") else None
        else:
            host, colon, port = hostport.partition(":")
            port = port if colon else None
        if not host:
            return None

        return SshTarget(host=host, path=f"/{path}", port=port or options["ssh_port"],
                         user=options["ssh_user"], identity=options["ssh_identity"])
    return None


def parse_received_uuids(output: str) -> set[str]:
    """received_uuid values in the output of `btrfs subvolume list -u -R`."""
    uuids = set()
    for line in output.splitlines():
        parts = line.split()
        if "received_uuid" in parts:
            idx = parts.index("received_uuid")
            if idx + 1 < len(parts) and parts[idx + 1] != "-":
                uuids.add(parts[idx + 1])
    return uuids


def parse_subvolume_uuid(output: str) -> str | None:
    """UUID in the output of `btrfs subvolume show`."""
    for line in output.splitlines():
        stripped = line.strip()
        # plain "UUID:" only — "Parent UUID:" and "Received UUID:" must not match
        if stripped.startswith("UUID:"):
            fields = stripped.split()
            if len(fields) >= 2:
                return fields[1]
    return None


class OutputLog:
    """btrbk output as shown on screen.

    Progress meters rewrite their line with '\\r': such a line is transient and
    the next one takes its place.
    """

    def __init__(self):
        self.lines: list[str] = []
        self.last_is_transient = False

    def push(self, text: str, transient: bool) -> bool:
        """Add a line; `transient` means it ended with '\\r'. Returns whether the screen changed."""
        cleaned = clean_output_line(text)
        if not cleaned.strip():
            # "\r\n": the newline makes the line before it permanent
            if not transient:
                self.last_is_transient = False
            return False
        if self.last_is_transient:
            self.lines.pop()
        self.lines.append(cleaned)
        self.last_is_transient = transient
        del self.lines[:-MAX_OUTPUT_LINES]
        return True


class StreamSplitter:
    """Splits a byte stream into lines on both '\\n' and '\\r'."""

    def __init__(self):
        self.pending = b""

    def feed(self, data: bytes) -> list[tuple[str, bool]]:
        """Return the (text, ended_with_cr) lines completed by `data`."""
        lines = []
        for byte in data:
            if byte in (10, 13):
                lines.append((self.pending.decode("utf-8", "replace"), byte == 13))
                self.pending = b""
            else:
                self.pending += bytes([byte])
        return lines

    def flush(self) -> list[tuple[str, bool]]:
        """Return whatever is left when the stream ends without a line ending."""
        if not self.pending:
            return []
        rest, self.pending = self.pending, b""
        return [(rest.decode("utf-8", "replace"), False)]


def last_line(output: bytes | str) -> str | None:
    """Last non-empty line of a command's output, cleaned for display."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    for line in reversed(output.splitlines()):
        cleaned = clean_output_line(line).strip()
        if cleaned:
            return cleaned
    return None


def run_command(cmd: list[str]) -> str | None:
    """Run a command silently.

    Returns None on success; on failure the last line it wrote to stderr, so
    the interface can say why.
    """
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except OSError as err:
        return f"cannot run {cmd[0]}: {err}"
    if result.returncode == 0:
        return None
    return last_line(result.stderr) or f"{cmd[0]} failed (exit {result.returncode})"


# --- configuration ------------------------------------------------------------

class Config:
    """Configuration manager for the application.

    `path` is the --config option; without it the file is looked up with
    config_candidates().
    """

    def __init__(self, path: Path | None = None):
        self.data = DEFAULT_CONFIG.copy()
        invoking = invoking_user()
        self.read_from: Path | None = None  # set when read from an old location
        if path is None:
            candidates = config_candidates(invoking.home if invoking else None, Path.home())
            found = next((c for c in candidates if c.is_file()), None)
            # An old location is read but never written: saving moves the
            # settings to the current name
            if found and not is_legacy_config(found):
                path = found
            else:
                path, self.read_from = candidates[0], found
        self.path = path
        # uid/gid to give a config written as root into the invoking user's home
        self.owner = (invoking.uid, invoking.gid) if invoking and path.is_relative_to(invoking.home) else None
        self.load()

    def load(self):
        """Load configuration from file."""
        try:
            with open(self.read_from or self.path) as f:
                saved_config = json.load(f)
            # Merge saved config with defaults (in case new keys were added)
            for key, value in saved_config.items():
                if key in DEFAULT_CONFIG:  # Only load known keys
                    self.data[key] = value
        except Exception:
            # Missing or unreadable: use defaults
            pass

    def save(self):
        """Save configuration to file."""
        # Directories about to be created, so that they can be handed over too
        created = [d for d in self.path.parents if not d.exists()]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            return False
        # Written as root into the invoking user's home: give it back to them,
        # or their next edit of their own file would need root
        if self.owner:
            for p in [*created, self.path]:
                with contextlib.suppress(OSError):
                    os.chown(p, *self.owner)
        self.read_from = None
        return True

    def get(self, key: str, default=None):
        """Get configuration value."""
        return self.data.get(key, default)

    def set(self, key: str, value):
        """Set configuration value."""
        if key in DEFAULT_CONFIG:  # Only allow known keys
            self.data[key] = value
            return True
        return False


# --- snapshot operations ------------------------------------------------------

class PurgeError(Exception):
    """The purge cannot be planned: nothing must be deleted."""


class SnapshotManager:
    """Manager for snapshot operations."""

    def __init__(self, config: Config):
        self.config = config

    def _snapshot_names(self) -> list[str]:
        snapshots_dir = self.config.get("snapshots_dir")
        return [name for name in os.listdir(snapshots_dir)
                if os.path.isdir(os.path.join(snapshots_dir, name))]

    def get_snapshots(self) -> list[tuple[str, list[str]]]:
        """Get available snapshots grouped by subvolume (dynamically detected)."""
        try:
            return group_snapshots(self._snapshot_names())
        except OSError:
            return []

    def format_snapshot_name(self, snapshot: str) -> str:
        """Format snapshot name for display."""
        if self.config.get("show_timestamps", True):
            parts = split_snapshot_name(snapshot)
            dt = parse_btrbk_timestamp(parts[1]) if parts else None
            if dt:
                return f"{snapshot} ({dt.strftime('%Y-%m-%d %H:%M:%S')})"
        return snapshot

    def restore_snapshot(self, snapshot: str, subvol_name: str) -> tuple[str, str]:
        """Replace the subvolume `subvol_name` with a writable snapshot of `snapshot`.

        `subvol_name` is the snapshot prefix as-is ("@", "@home", "@root", ...):
        deriving it from a type name would map a subvolume called @root onto @.

        Returns (outcome, reason), outcome being one of:
            "success"         - restore completato e verificato
            "failed"          - restore fallito ma rollback riuscito (stato precedente ripristinato)
            "rollback_failed" - restore fallito E rollback fallito (stato incoerente, .BROKEN conservato)
        """
        btr_pool_dir = self.config.get("btr_pool_dir")
        source_path = os.path.join(self.config.get("snapshots_dir"), snapshot)

        # Pre-check: lo snapshot sorgente deve esistere prima di toccare il subvolume corrente
        if not os.path.exists(source_path):
            return "failed", f"{source_path} no longer exists"

        current_subvol = os.path.join(btr_pool_dir, subvol_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        broken_subvol = os.path.join(btr_pool_dir, f"{subvol_name}.BROKEN.{timestamp}")

        current_existed = os.path.exists(current_subvol)

        if current_existed:
            # Guardia: deve essere un vero subvolume btrfs prima di spostarlo
            # (evita di spostare una directory normale per errore)
            reason = run_command(["btrfs", "subvolume", "show", current_subvol])
            if reason:
                return "failed", f"{current_subvol} is not a btrfs subvolume: {reason}"

            # rename(2) would silently replace an empty directory
            if os.path.exists(broken_subvol):
                return "failed", f"{broken_subvol} already exists"

            # Move current to .BROKEN. rename(2) e non mv: tra filesystem
            # diversi fallisce, invece di mettersi a copiare un subvolume
            try:
                os.rename(current_subvol, broken_subvol)
            except OSError as err:
                return "failed", f"cannot move {subvol_name}: {err}"

        def roll_back(reason: str) -> tuple[str, str]:
            """Rimette al suo posto il subvolume originale."""
            if current_existed:
                try:
                    os.rename(broken_subvol, current_subvol)
                except OSError as err:
                    return "rollback_failed", f"{reason}; original kept as {broken_subvol} ({err})"
            return "failed", reason

        # Create new snapshot
        reason = run_command(["btrfs", "subvolume", "snapshot", source_path, current_subvol])
        if reason:
            return roll_back(f"snapshot failed: {reason}")

        # Verify restore success
        reason = self._verify_restore_success(current_subvol, subvol_name)
        if reason:
            # Rollback completo: rimuovi il subvolume fallito e ripristina l'originale
            err = run_command(["btrfs", "subvolume", "delete", current_subvol])
            if err:
                # Il subvolume fallito occupa ancora il path: impossibile ripristinare l'originale
                return "rollback_failed", (f"{reason}; cannot remove the failed restore ({err}), "
                                           f"original kept as {broken_subvol}")
            return roll_back(reason)

        # Auto cleanup if enabled (solo se avevamo un originale da rimuovere)
        if self.config.get("auto_cleanup", False) and current_existed:
            run_command(["btrfs", "subvolume", "delete", broken_subvol])

        return "success", ""

    def _verify_restore_success(self, restored_subvol: str, subvol_name: str) -> str | None:
        """Verify restored subvolume integrity. Returns None when fine, else the reason."""
        if not os.path.exists(restored_subvol):
            return "restored subvolume is missing"

        # Verify it's a valid btrfs subvolume
        reason = run_command(["btrfs", "subvolume", "show", restored_subvol])
        if reason:
            return f"restored path is not a subvolume: {reason}"

        # Root and home are recognised by the names "@"/"@home" or by what is
        # mounted on / and /home: layouts without "@" call them root, rootfs, home...
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            mountinfo = ""
        if subvol_name == "@" or mounted_subvolume(mountinfo, "/") == subvol_name:
            kind = "root"
        elif subvol_name == "@home" or mounted_subvolume(mountinfo, "/home") == subvol_name:
            kind = "home"
        else:
            kind = "other"

        try:
            if kind == "root":
                for d in ["etc", "usr", "var", "bin"]:
                    if not os.path.exists(os.path.join(restored_subvol, d)):
                        return f"restored root has no /{d}"
                for f in ["etc/fstab", "etc/passwd"]:
                    if not os.path.isfile(os.path.join(restored_subvol, f)):
                        return f"restored root has no /{f}"
            elif kind == "home":
                if not os.listdir(restored_subvol):
                    return "restored home is empty"
            else:
                # Any other subvolume: just verify readable
                os.listdir(restored_subvol)
        except OSError as err:
            return f"restored subvolume is unreadable: {err}"

        return None

    def get_target_received_uuids(self) -> set[str]:
        """received_uuid of every subvolume present on the backup target.

        Raises PurgeError when the target is not configured or not reachable.
        The caller must then refuse to purge: without knowing what the target
        holds, there is no way to tell which snapshot is still needed as
        parent for the next incremental send.
        """
        try:
            conf = Path(BTRBK_CONF).read_text()
        except OSError as err:
            raise PurgeError(f"cannot read {BTRBK_CONF}: {err}") from err
        target = parse_ssh_target(conf)
        if target is None:
            raise PurgeError(f"no ssh target in {BTRBK_CONF}")

        command = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
        if target.port:
            command += ["-p", target.port]
        if target.user:
            command += ["-l", target.user]
        if target.identity:
            command += ["-i", target.identity]
        quoted_path = target.path.replace("'", "'\\''")
        command += [target.host, f"sudo btrfs subvolume list -u -R '{quoted_path}'"]

        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=60)
        except (subprocess.SubprocessError, OSError) as err:
            raise PurgeError(f"backup target unreachable: {err}") from err
        if result.returncode != 0:
            raise PurgeError(f"backup target unreachable: {last_line(result.stderr) or 'ssh failed'}")

        return parse_received_uuids(result.stdout)

    def get_local_uuid(self, snapshot_path: str) -> str | None:
        """UUID of a local subvolume, or None if it cannot be read."""
        try:
            result = subprocess.run(["btrfs", "subvolume", "show", snapshot_path],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True)
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return parse_subvolume_uuid(result.stdout)

    def plan_purge(self) -> PurgePlan:
        """Work out which old snapshots can go, keeping the ones the target still needs.

        btrbk does not protect snapshots that serve as parent for incremental
        backups (see btrbk.conf(5)), so deleting the newest snapshot the target
        also holds forces the next run into a full send — for a large subvolume
        that means hours of transfer. We therefore keep the newest snapshot
        present on the target *and* everything after it.

        Raises PurgeError when the target cannot be queried.
        """
        target_uuids = self.get_target_received_uuids()
        snapshots_dir = self.config.get("snapshots_dir")
        try:
            names = self._snapshot_names()
        except OSError as err:
            raise PurgeError(f"cannot read snapshots directory: {err}") from err

        groups = [
            (prefix, [(name, self.get_local_uuid(os.path.join(snapshots_dir, name)))
                      for name in reversed(snapshots)])  # oldest first
            for prefix, snapshots in group_snapshots(names)
        ]
        return compute_purge_plan(groups, target_uuids)

    def execute_purge(self, plan: PurgePlan) -> tuple[int, int]:
        """Delete the planned snapshots. Returns (deleted, failed)."""
        snapshots_dir = self.config.get("snapshots_dir")
        deleted = sum(
            run_command(["btrfs", "subvolume", "delete", os.path.join(snapshots_dir, name)]) is None
            for name in plan.delete
        )
        return deleted, len(plan.delete) - deleted

    def clean_broken_subvolumes(self) -> tuple[int, int]:
        """Delete every .BROKEN subvolume in the pool. Returns (deleted, failed); raises OSError."""
        btr_pool_dir = self.config.get("btr_pool_dir")
        broken = [os.path.join(btr_pool_dir, item) for item in os.listdir(btr_pool_dir)
                  if ".BROKEN" in item and os.path.isdir(os.path.join(btr_pool_dir, item))]
        deleted = sum(run_command(["btrfs", "subvolume", "delete", path]) is None for path in broken)
        return deleted, len(broken) - deleted


# --- user interface -----------------------------------------------------------

def put(stdscr, y: int, x: int, text: str, attr: int = 0):
    """Write `text` at (y, x), clipped at the right edge.

    Off-screen coordinates are ignored: no drawing can raise or wrap, whatever
    the terminal size.
    """
    height, width = stdscr.getmaxyx()
    if y < 0 or x < 0 or y >= height or x >= width:
        return
    # curses raises after writing the bottom-right cell, though the cell is drawn
    with contextlib.suppress(curses.error):
        stdscr.addstr(y, x, text[:width - x], attr)


def put_centered(stdscr, y: int, text: str, attr: int = 0):
    _, width = stdscr.getmaxyx()
    put(stdscr, y, max(0, (width - len(text)) // 2), text, attr)


def put_separator(stdscr, y: int):
    _, width = stdscr.getmaxyx()
    put(stdscr, y, 0, "-" * width)


def key_char(key: int) -> str | None:
    """Lowercase ASCII letter for a key code, if it is one."""
    if 0 <= key < 128 and chr(key).isalpha():
        return chr(key).lower()
    return None


class TUIApp:
    """Main TUI application."""

    def __init__(self, config_file: Path | None = None):
        self.config = Config(config_file)
        self.snapshot_manager = SnapshotManager(self.config)
        self.current_screen = "main"
        self.selected_row = 0
        self.selected_col = 0
        self.status_message = ""
        self.status_until = 0.0
        self.reboot_needed = False  # Track if reboot is needed
        # Cache degli snapshot: evita di rileggere il filesystem ad ogni frame.
        # None = cache invalidata (verrà ricalcolata al prossimo accesso).
        self._snapshot_cache: list[tuple[str, list[str]]] | None = None

    def get_snapshots_cached(self) -> list[tuple[str, list[str]]]:
        """Restituisce gli snapshot dalla cache, ricalcolandoli solo se invalidata."""
        if self._snapshot_cache is None:
            self._snapshot_cache = self.snapshot_manager.get_snapshots()
        return self._snapshot_cache

    def invalidate_snapshots(self):
        """Invalida la cache: il prossimo accesso rileggerà il filesystem."""
        self._snapshot_cache = None

    def init_colors(self):
        """Initialize color pairs."""
        curses.start_color()
        curses.use_default_colors()

        # Color pairs
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Selected item
        curses.init_pair(2, curses.COLOR_RED, -1)                    # Headers
        curses.init_pair(3, curses.COLOR_GREEN, -1)                  # Success
        curses.init_pair(4, curses.COLOR_YELLOW, -1)                 # Warning
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Status bar
        curses.init_pair(6, curses.COLOR_CYAN, -1)                   # Info

    def set_status(self, message: str, seconds: float = STATUS_SHORT):
        """Set status message, shown for `seconds`."""
        self.status_message = message
        self.status_until = time.monotonic() + seconds

    def show_busy(self, stdscr, message: str):
        """Show a message right away, before an operation that blocks the interface.

        set_status alone is not enough: the message would only be drawn on the
        next frame, that is once the operation is already over.
        """
        self.set_status(message)
        self.draw_screen(stdscr)
        stdscr.refresh()

    def draw_header(self, stdscr):
        """Draw application header."""
        _, width = stdscr.getmaxyx()
        put(stdscr, 0, 0, f"BTRBK TUI v{VERSION}".center(width), curses.color_pair(5) | curses.A_BOLD)
        put_separator(stdscr, 1)

    def draw_footer(self, stdscr):
        """Draw application footer with key bindings."""
        height, _ = stdscr.getmaxyx()

        if self.current_screen == "main":
            keys = [
                "Up/Down: Navigate", "Left/Right: Switch", "ENTER: Restore",
                "S: Settings", "R: Refresh", "I: Snapshot", "P: Purge OLD", "B: Clean BROKEN",
            ]
            if self.reboot_needed:
                keys.append("H: REBOOT")
            keys.append("Q: Quit")
            footer_text = " | ".join(keys)
        else:
            footer_text = "Up/Down: Navigate | ENTER: Edit | SPACE: Toggle | S: Save | ESC: Back | Q: Quit"

        put_separator(stdscr, height - 2)
        put(stdscr, height - 1, 0, footer_text, curses.color_pair(5))

    def draw_status(self, stdscr):
        """Draw status message if any."""
        height, _ = stdscr.getmaxyx()

        if self.status_message and time.monotonic() < self.status_until:
            put(stdscr, height - 3, 0, self.status_message, curses.color_pair(6))
            return

        self.status_message = ""
        # Show reboot warning only when no temporary messages are active
        if self.reboot_needed:
            put(stdscr, height - 3, 0, "WARNING: REBOOT REQUIRED - Press H to reboot system",
                curses.color_pair(4) | curses.A_BOLD)

    def clamp_selection(self, groups):
        """Bring the selection back within bounds.

        Lists change after refresh, purge and restore, and a selection past
        the end of a list would be invisible.
        """
        self.selected_col = max(0, min(self.selected_col, len(groups) - 1))
        rows = len(groups[self.selected_col][1]) if groups else 0
        self.selected_row = max(0, min(self.selected_row, rows - 1))

    def draw_main_screen(self, stdscr):
        """Draw main snapshot selection screen with dynamic columns."""
        height, width = stdscr.getmaxyx()
        groups = self.get_snapshots_cached()

        # Show current configuration
        config_info = f"Pool: {self.config.get('btr_pool_dir')} | Snapshots: {self.config.get('snapshots_dir')}"
        put(stdscr, 2, 2, config_info, curses.A_DIM)

        if not groups:
            put_centered(stdscr, height // 2, "No snapshots found!", curses.color_pair(4) | curses.A_BOLD)
            put_centered(stdscr, height // 2 + 1, "S: check the paths | I: create snapshots", curses.A_DIM)
            return

        self.clamp_selection(groups)

        # Calculate column positions dynamically
        col_width = max(1, (width - 4) // len(groups))
        text_width = max(1, col_width - 2)
        start_y = 4
        # Rows from start_y down to the line above the status bar; the row in
        # between is kept for the "more below" indicator
        visible = max(1, height - 4 - start_y - 1)

        for col_idx, (prefix, snapshots) in enumerate(groups):
            col_x = 2 + col_idx * col_width
            header = f"{prefix.upper()} ({len(snapshots)})"
            put(stdscr, start_y - 1, col_x, header[:text_width], curses.color_pair(2) | curses.A_BOLD)

            # Only the selected column scrolls, just enough to keep the cursor visible
            first = max(0, self.selected_row + 1 - visible) if col_idx == self.selected_col else 0

            for row in range(first, min(first + visible, len(snapshots))):
                display_name = self.snapshot_manager.format_snapshot_name(snapshots[row])
                selected = col_idx == self.selected_col and row == self.selected_row
                put(stdscr, start_y + row - first, col_x, display_name[:text_width],
                    curses.color_pair(1) if selected else 0)

            if len(snapshots) > visible:
                last = min(first + visible, len(snapshots))
                position = f"[{first + 1}-{last} of {len(snapshots)}]"
                put(stdscr, start_y + visible, col_x, position[:text_width], curses.A_DIM)

    def draw_settings_screen(self, stdscr):
        """Draw settings configuration screen."""
        height, _ = stdscr.getmaxyx()
        start_y = 4

        put(stdscr, start_y - 1, 4, "SETTINGS", curses.color_pair(2) | curses.A_BOLD)

        for i, (label, key) in enumerate(SETTINGS):
            y = start_y + i * 2
            if y >= height - 6:  # Don't write too close to bottom
                break

            value = self.config.get(key)
            value_str = ("Yes" if value else "No") if isinstance(value, bool) else str(value)
            attr = curses.color_pair(1) if i == self.selected_row else 0
            put(stdscr, y, 4, f"{label}:", attr)
            put(stdscr, y + 1, 6, value_str, attr)

        # Show config file path and status
        config_exists = "EXISTS" if self.config.path.exists() else "NOT FOUND"
        put(stdscr, height - 5, 4, f"Config: {self.config.path} ({config_exists})", curses.A_DIM)
        if self.config.read_from:
            put(stdscr, height - 4, 4, f"Read from the old location {self.config.read_from}: saving moves it",
                curses.A_DIM)

    def draw_screen(self, stdscr):
        """Draw the whole interface."""
        # erase() and not clear(): clear() forces a full terminal repaint on
        # every refresh, which means flicker on every frame
        stdscr.erase()
        self.draw_header(stdscr)
        if self.current_screen == "main":
            self.draw_main_screen(stdscr)
        else:
            self.draw_settings_screen(stdscr)
        self.draw_status(stdscr)
        self.draw_footer(stdscr)

    def create_snapshot(self, stdscr) -> str | None:
        """Run `btrbk run --progress`, streaming its output. Returns None on success, else the reason."""
        height, _ = stdscr.getmaxyx()

        stdscr.erase()
        self.draw_header(stdscr)
        put_centered(stdscr, 4, "Creating Snapshots with btrbk...", curses.color_pair(2) | curses.A_BOLD)
        put_centered(stdscr, 6, "Press ESC to cancel or wait for completion", curses.A_DIM)

        # Simple output area - only horizontal borders
        output_start_y = 8
        output_height = max(1, height - 12)
        put_separator(stdscr, output_start_y - 1)
        put_separator(stdscr, output_start_y + output_height)
        stdscr.refresh()

        try:
            # Own session: no controlling terminal, so a stray ssh password
            # prompt fails instead of scribbling over the curses screen, and
            # the whole process tree (btrfs send, ssh, pv) can be signalled at
            # once on cancel.
            process = subprocess.Popen(["btrbk", "run", "--progress"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
        except OSError as err:
            return f"cannot run btrbk: {err}"

        streams = {process.stdout.fileno(): StreamSplitter(), process.stderr.fileno(): StreamSplitter()}
        log = OutputLog()
        cancelled_at = exited_at = None
        killed = False

        stdscr.timeout(50)
        try:
            while True:
                if stdscr.getch() == KEY_ESC and cancelled_at is None:
                    # SIGINT to the whole group is what Ctrl-C does in a shell,
                    # the case btrbk is written for: it aborts and logs the
                    # transaction.
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGINT)
                    cancelled_at = time.monotonic()
                    put_centered(stdscr, height - 2, "Cancelling, waiting for btrbk to stop...",
                                 curses.color_pair(4) | curses.A_BOLD)
                    stdscr.refresh()

                if cancelled_at and not killed and time.monotonic() - cancelled_at > 5:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    killed = True

                dirty = False
                readable = select.select(list(streams), [], [], 0)[0] if streams else []
                for fd in readable:
                    data = os.read(fd, 65536)
                    lines = streams[fd].feed(data) if data else streams.pop(fd).flush()
                    for text, transient in lines:
                        dirty |= log.push(text, transient)
                if dirty:
                    self.render_output_area(stdscr, log.lines, output_start_y, output_height)
                    stdscr.refresh()

                # Leave only once btrbk is gone, so the SIGKILL escalation above
                # stays armed. If something still holds the pipes open after it
                # exited, do not wait for it forever.
                if exited_at is None and process.poll() is not None:
                    exited_at = time.monotonic()
                if exited_at and (not streams or (not dirty and time.monotonic() - exited_at > 0.5)):
                    break
        finally:
            stdscr.timeout(100)
            process.stdout.close()
            process.stderr.close()

        succeeded = process.wait() == 0

        if cancelled_at:
            # btrbk has cleaned up and gone: whatever ignored SIGINT must not
            # outlive the cancel as an orphan running as root
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            return "cancelled by user"

        # ASCII only: under pkexec the locale is C and glyphs would be garbled
        if succeeded:
            put_centered(stdscr, height - 2, "[OK] Snapshots created successfully! Press any key to continue...",
                         curses.color_pair(3) | curses.A_BOLD)
        else:
            put_centered(stdscr, height - 2, "[FAILED] Error creating snapshots! Press any key to continue...",
                         curses.color_pair(4) | curses.A_BOLD)
        stdscr.refresh()

        stdscr.timeout(-1)
        stdscr.getch()
        stdscr.timeout(100)

        if succeeded:
            return None
        return log.lines[-1] if log.lines else "btrbk exited with an error"

    def render_output_area(self, stdscr, lines: list[str], start_y: int, height: int):
        """Redraw the output area with the last `height` lines, clearing leftovers first."""
        _, width = stdscr.getmaxyx()
        for row in range(height):
            put(stdscr, start_y + row, 0, " " * width)
        for idx, line in enumerate(lines[-height:]):
            put(stdscr, start_y + idx, 0, line)

    def confirm_dialog(self, stdscr, message: str) -> bool:
        """Show confirmation dialog."""
        if not self.config.get("confirm_actions", True):
            return True

        height, width = stdscr.getmaxyx()
        hint = "Y: Yes | N: No"
        lines = message.splitlines()
        dialog_width = max(8, min(max([*map(len, lines), len(hint)]) + 6, width - 4))
        dialog_height = len(lines) + 4  # border, message, blank, hint, border
        dialog_y = height // 2 - dialog_height // 2
        dialog_x = max(0, (width - dialog_width) // 2)
        inner = dialog_width - 2

        border = f"+{'-' * inner}+"
        put(stdscr, dialog_y, dialog_x, border, curses.A_BOLD)
        for i in range(1, dialog_height - 1):
            put(stdscr, dialog_y + i, dialog_x, f"|{' ' * inner}|", curses.A_BOLD)
        put(stdscr, dialog_y + dialog_height - 1, dialog_x, border, curses.A_BOLD)
        for i, line in enumerate(lines):
            put(stdscr, dialog_y + 1 + i, dialog_x + 3, line[:max(0, inner - 4)])
        put(stdscr, dialog_y + dialog_height - 2, dialog_x + 3, hint[:max(0, inner - 4)])
        stdscr.refresh()

        stdscr.timeout(-1)
        try:
            while True:
                key = stdscr.getch()
                if key_char(key) == "y":
                    return True
                if key == KEY_ESC or key_char(key) == "n":
                    return False
        finally:
            stdscr.timeout(100)

    def edit_setting(self, stdscr, key: str):
        """Edit a configuration setting."""
        current_value = self.config.get(key)

        if isinstance(current_value, bool):
            self.toggle_setting(key)
            return

        height, width = stdscr.getmaxyx()

        # Clear area for input
        for i in range(5):
            put(stdscr, height // 2 - 2 + i, 4, " " * max(0, width - 8))

        input_y = height // 2 + 1
        input_x = 9
        input_width = max(1, width - input_x - 4)
        put(stdscr, height // 2 - 1, 4, f"Edit {key}: ")
        put(stdscr, height // 2, 4, f"Current: {current_value}")
        put(stdscr, input_y, 4, "New: ")
        put(stdscr, height // 2 + 3, 4, "Press ENTER to confirm, ESC to cancel")

        curses.curs_set(1)
        stdscr.timeout(-1)
        text = ""
        try:
            while True:
                # When the text does not fit, show its tail: where the typing happens
                tail = text[-input_width:]
                put(stdscr, input_y, input_x, " " * input_width)
                put(stdscr, input_y, input_x, tail)
                with contextlib.suppress(curses.error):
                    stdscr.move(input_y, input_x + len(tail))
                stdscr.refresh()

                ch = stdscr.getch()
                if ch in ENTER_KEYS:
                    confirmed = True
                    break
                if ch == KEY_ESC:
                    confirmed = False
                    break
                if ch in BACKSPACE_KEYS:
                    text = text[:-1]
                elif 32 <= ch < 127:
                    text += chr(ch)
        finally:
            stdscr.timeout(100)
            curses.curs_set(0)

        new_path = text.strip()
        if not confirmed or not new_path:
            self.set_status("Edit cancelled")
            return

        self.config.set(key, new_path)
        self.invalidate_snapshots()
        if not self.config.save():
            self.set_status(f"Updated {key} but could NOT save the config file", STATUS_LONG)
        elif os.path.isdir(new_path):
            self.set_status(f"Updated {key}", STATUS_MEDIUM)
        else:
            # Avvisa se un path di directory non esiste
            self.set_status(f"Updated {key} (WARNING: path does not exist)", STATUS_LONG)

    def toggle_setting(self, key: str):
        """Toggle a boolean setting and save."""
        value = self.config.get(key)
        if not isinstance(value, bool):
            return
        self.config.set(key, not value)
        label = next(label for label, name in SETTINGS if name == key)
        state = "No" if value else "Yes"
        if self.config.save():
            self.set_status(f"{label}: {state}", STATUS_MEDIUM)
        else:
            self.set_status(f"{label}: {state} (could NOT save the config file)", STATUS_LONG)

    def handle_main_input(self, stdscr, key):
        """Handle input for main screen with dynamic columns."""
        groups = self.get_snapshots_cached()
        self.clamp_selection(groups)

        if key == curses.KEY_UP:
            self.selected_row -= 1
        elif key == curses.KEY_DOWN:
            self.selected_row += 1
        elif key == curses.KEY_LEFT:
            self.selected_col -= 1
        elif key == curses.KEY_RIGHT:
            self.selected_col += 1
        elif key == curses.KEY_HOME:
            self.selected_row = 0
        elif key == curses.KEY_END:
            self.selected_row = sys.maxsize
        elif key in ENTER_KEYS:
            self.handle_snapshot_selection(stdscr, groups)
            return
        else:
            action = {
                "s": self.open_settings,
                "r": self.handle_refresh,
                "h": self.handle_reboot,
                "p": self.handle_purge,
                "b": self.handle_clean_broken,
                "i": self.handle_create_snapshot,
            }.get(key_char(key))
            if action:
                action(stdscr)
            return
        self.clamp_selection(groups)

    def open_settings(self, _stdscr):
        self.current_screen = "settings"
        self.selected_row = 0

    def handle_refresh(self, _stdscr):
        # Invalidate cache and force re-read from filesystem
        self.invalidate_snapshots()
        self.set_status("Snapshots refreshed")

    def handle_reboot(self, stdscr):
        if not self.reboot_needed:
            self.set_status("No reboot needed")
        elif self.confirm_dialog(stdscr, "Reboot system now?"):
            run_command(["sync"])
            reason = run_command(["reboot"])
            if reason:
                self.set_status(f"Error: reboot failed: {reason}", STATUS_LONG)
        else:
            self.set_status("Reboot cancelled")

    def handle_purge(self, stdscr):
        # querying the target over ssh takes a moment: say so before blocking
        self.show_busy(stdscr, "Checking backup target...")

        try:
            plan = self.snapshot_manager.plan_purge()
        except PurgeError as err:
            self.set_status(f"Nothing purged (chain left intact): {err}", STATUS_RESULT)
            return

        skipped = f" ({', '.join(plan.skipped)} skipped: not on the backup target)" if plan.skipped else ""

        if not plan.delete:
            self.set_status(f"No old snapshots to purge{skipped}", STATUS_LONG)
            return

        self.draw_screen(stdscr)
        if not self.confirm_dialog(stdscr, f"Delete {len(plan.delete)} old snapshots? The backup chain is kept."):
            self.set_status("Purge cancelled")
            return

        self.show_busy(stdscr, "Purging old snapshots...")
        deleted, failed = self.snapshot_manager.execute_purge(plan)
        self.invalidate_snapshots()

        if failed:
            self.set_status(f"Purged {deleted} old snapshots, {failed} could NOT be deleted{skipped}", STATUS_RESULT)
        else:
            self.set_status(f"Purged {deleted} old snapshots{skipped}", STATUS_RESULT)

    def handle_clean_broken(self, stdscr):
        if not self.confirm_dialog(stdscr, "Delete all .BROKEN subvolumes?"):
            self.set_status("Clean cancelled")
            return

        self.show_busy(stdscr, "Cleaning .BROKEN subvolumes...")
        try:
            deleted, failed = self.snapshot_manager.clean_broken_subvolumes()
        except OSError as err:
            self.set_status(f"Error: cannot read pool directory: {err}", STATUS_LONG)
            return

        if failed:
            self.set_status(f"Cleaned {deleted} .BROKEN subvolumes, {failed} could NOT be deleted (still mounted?)",
                            STATUS_RESULT)
        elif deleted:
            self.set_status(f"Cleaned {deleted} .BROKEN subvolumes", STATUS_RESULT)
        else:
            self.set_status("No .BROKEN subvolumes found", STATUS_MEDIUM)

    def handle_create_snapshot(self, stdscr):
        if not self.confirm_dialog(stdscr, "Create new snapshots with btrbk?"):
            self.set_status("Snapshot creation cancelled")
            return

        reason = self.create_snapshot(stdscr)
        # even an interrupted run may have created snapshots already
        self.invalidate_snapshots()
        if reason:
            self.set_status(f"Snapshot creation failed: {reason}", STATUS_RESULT)
        else:
            self.set_status("Snapshots created successfully", STATUS_LONG)

    def handle_snapshot_selection(self, stdscr, groups):
        """Handle snapshot selection and restoration with dynamic columns."""
        if not groups or not groups[self.selected_col][1]:
            return

        # The subvolume to replace is the snapshot prefix, untouched:
        # "@" -> @, "@home" -> @home, "@root" -> @root (never mistaken for "@")
        subvol_name, snapshots = groups[self.selected_col]
        snapshot = snapshots[self.selected_row]

        # The dialog names the path that will be touched: with a wrong pool in
        # the settings a new subvolume would be created instead of replaced
        target = os.path.join(self.config.get("btr_pool_dir"), subvol_name)
        if os.path.exists(target):
            effect = f"Replaces {target} (the old one is kept as .BROKEN)"
        else:
            effect = f"WARNING: {target} does not exist, it will be CREATED"
        if not self.confirm_dialog(stdscr, f"Restore {subvol_name} from {snapshot}?\n{effect}"):
            self.set_status("Restore cancelled")
            return

        self.show_busy(stdscr, f"Restoring {subvol_name}...")

        outcome, reason = self.snapshot_manager.restore_snapshot(snapshot, subvol_name)
        if outcome == "success":
            self.reboot_needed = True
            self.set_status(f"{subvol_name} restored! Press H to reboot when ready", STATUS_RESULT)
        elif outcome == "rollback_failed":
            self.set_status(f"CRITICAL: {subvol_name} restore AND rollback failed, manual recovery needed: {reason}",
                            STATUS_CRITICAL)
        else:
            self.set_status(f"Error: {subvol_name} restore failed, rolled back: {reason}", STATUS_RESULT)
        self.invalidate_snapshots()

    def handle_settings_input(self, stdscr, key):
        """Handle input for settings screen."""
        setting = SETTINGS[self.selected_row][1]

        if key == curses.KEY_UP:
            self.selected_row = max(0, self.selected_row - 1)
        elif key == curses.KEY_DOWN:
            self.selected_row = min(len(SETTINGS) - 1, self.selected_row + 1)
        elif key in ENTER_KEYS:
            self.edit_setting(stdscr, setting)
        elif key == ord(' '):  # Space to toggle boolean values
            self.toggle_setting(setting)
        elif key_char(key) == "s":
            # Manual save (though auto-save is already active)
            if self.config.save():
                self.set_status("Configuration saved", STATUS_MEDIUM)
            else:
                self.set_status("Error: failed to save configuration", STATUS_LONG)
        elif key == KEY_ESC:
            self.current_screen = "main"
            self.selected_row = 0

    def run(self, stdscr):
        """Main application loop."""
        curses.curs_set(0)
        stdscr.timeout(100)  # Non-blocking input with timeout

        self.init_colors()

        while True:
            self.draw_screen(stdscr)
            stdscr.refresh()

            # Handle input
            key = stdscr.getch()

            if key == -1:  # Timeout, continue loop
                continue
            if key_char(key) == "q":
                break
            if self.current_screen == "main":
                self.handle_main_input(stdscr, key)
            else:
                self.handle_settings_input(stdscr, key)


def print_purge_plan(config_file: Path | None) -> int:
    """Print what a purge would delete, without deleting anything."""
    try:
        plan = SnapshotManager(Config(config_file)).plan_purge()
    except PurgeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    if not plan.delete:
        print("Nothing to purge.")
    for name in plan.delete:
        print(f"would delete  {name}")
    for prefix in plan.skipped:
        print(f"skipped       {prefix} (no snapshot in common with the backup target)")
    return 0


def main():
    """Main entry point."""
    purge_plan = False
    config_file = None
    args = iter(sys.argv[1:])
    for arg in args:
        if arg == "--purge-plan":
            purge_plan = True
        elif arg in ("--config", "-c"):
            value = next(args, None)
            if value is None:
                print(f"Error: {arg} needs a file (try --help)", file=sys.stderr)
                sys.exit(2)
            config_file = Path(value)
        elif arg in ("--version", "-V"):
            print(f"btrbk_tui_pro {VERSION}")
            return
        elif arg in ("--help", "-h"):
            print(f"btrbk_tui_pro {VERSION} - restore Btrfs snapshots created with btrbk\n")
            print("Usage: sudo ./btrbk_tui_pro.py [OPTION]...\n")
            print("  -c, --config FILE  use FILE as configuration (default: ~/.config/btrbk_tui/config.json")
            print("                     of the user who ran sudo)")
            print("  --purge-plan       show what Purge OLD would delete, then exit")
            print("  -V, --version      show the version")
            print("  -h, --help         show this help")
            return
        else:
            print(f"Error: unknown option '{arg}' (try --help)", file=sys.stderr)
            sys.exit(2)

    if os.geteuid() != 0:
        print("Error: This tool requires root privileges.")
        print("Please run with sudo.")
        sys.exit(1)

    if purge_plan:
        sys.exit(print_purge_plan(config_file))

    # Without this curses cannot draw non-ASCII snapshot names
    with contextlib.suppress(locale.Error):
        locale.setlocale(locale.LC_ALL, "")
    # Without this ESC is only recognised after a full second
    os.environ.setdefault("ESCDELAY", "25")

    try:
        app = TUIApp(config_file)
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
