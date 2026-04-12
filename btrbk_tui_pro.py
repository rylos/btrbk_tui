#!/usr/bin/env python3
"""
BTRBK Restore Tool - Professional TUI Version
A professional terminal user interface for restoring Btrfs snapshots created with btrbk.
"""

import curses
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Configuration file path
CONFIG_FILE = Path.home() / ".config" / "btrbk_tui" / "config.json"

# Default configuration
DEFAULT_CONFIG = {
    "btr_pool_dir": "/mnt/btr_pool",
    "snapshots_dir": "/mnt/btr_pool/btrbk_snapshots",
    "auto_cleanup": False,
    "confirm_actions": True,
    "show_timestamps": True,
    "theme": "default"
}

class Config:
    """Configuration manager for the application."""
    
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()
    
    def load(self):
        """Load configuration from file."""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r') as f:
                    saved_config = json.load(f)
                    # Merge saved config with defaults (in case new keys were added)
                    for key, value in saved_config.items():
                        if key in DEFAULT_CONFIG:  # Only load known keys
                            self.data[key] = value
        except Exception as e:
            # If loading fails, use defaults
            pass
    
    def save(self):
        """Save configuration to file."""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
            return True
        except Exception as e:
            return False
    
    def get(self, key: str, default=None):
        """Get configuration value."""
        return self.data.get(key, default)
    
    def set(self, key: str, value):
        """Set configuration value."""
        if key in DEFAULT_CONFIG:  # Only allow known keys
            self.data[key] = value
            return True
        return False

class SnapshotManager:
    """Manager for snapshot operations."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def get_snapshots(self) -> Tuple[Dict[str, List[str]], List[str]]:
        """Get available snapshots organized by type (dynamically detected)."""
        snapshots_dir = self.config.get("snapshots_dir")
        try:
            folders = [f for f in os.listdir(snapshots_dir) 
                      if os.path.isdir(os.path.join(snapshots_dir, f))]
            
            # Group snapshots by prefix
            snapshot_groups = {}
            for folder in folders:
                if '.' in folder and folder.startswith('@'):
                    prefix = folder.split('.')[0]
                    if prefix not in snapshot_groups:
                        snapshot_groups[prefix] = []
                    snapshot_groups[prefix].append(folder)
            
            # Sort each group by timestamp (newest first)
            for prefix in snapshot_groups:
                snapshot_groups[prefix].sort(reverse=True)
            
            # Sort prefixes for consistent ordering (@ first, then alphabetically)
            sorted_prefixes = sorted(snapshot_groups.keys(), key=lambda x: (x != '@', x))
            
            return snapshot_groups, sorted_prefixes
        except Exception:
            return {}, []
    
    def format_snapshot_name(self, snapshot: str) -> str:
        """Format snapshot name for display."""
        if not self.config.get("show_timestamps", True):
            return snapshot
        
        # Extract timestamp from snapshot name
        try:
            if '.' in snapshot and snapshot.startswith('@'):
                # Find the prefix and extract timestamp
                prefix = snapshot.split('.')[0]
                timestamp_str = snapshot[len(prefix) + 1:]  # +1 for the dot
                
                # Try multiple timestamp formats
                try:
                    dt = datetime.strptime(timestamp_str, "%Y%m%dT%H%M")
                    return f"{snapshot} ({dt.strftime('%Y-%m-%d %H:%M:%S')})"
                except ValueError:
                    try:
                        dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                        return f"{snapshot} ({dt.strftime('%Y-%m-%d %H:%M:%S')})"
                    except ValueError:
                        return snapshot
            else:
                return snapshot
        except (ValueError, IndexError):
            return snapshot
        except ValueError:
            return snapshot
    
    def restore_snapshot(self, snapshot: str, snapshot_type: str) -> bool:
        """Restore a snapshot with verification and rollback."""
        btr_pool_dir = self.config.get("btr_pool_dir")
        snapshots_dir = self.config.get("snapshots_dir")
        
        source_path = os.path.join(snapshots_dir, snapshot)
        
        # Dynamic subvolume path generation
        if snapshot_type == "root" or snapshot_type == "":
            subvol_name = "@"
        else:
            subvol_name = f"@{snapshot_type}"
        
        current_subvol = os.path.join(btr_pool_dir, subvol_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        broken_subvol = os.path.join(btr_pool_dir, f"{subvol_name}.BROKEN.{timestamp}")
        new_subvol = os.path.join(btr_pool_dir, subvol_name)
        
        try:
            # Move current subvolume to .BROKEN
            subprocess.run(["mv", current_subvol, broken_subvol], 
                         check=True, capture_output=True, text=True)
            
            # Create new snapshot
            try:
                subprocess.run(["btrfs", "subvolume", "snapshot", source_path, new_subvol], 
                             check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError:
                # Rollback: restore original
                subprocess.run(["mv", broken_subvol, current_subvol], capture_output=True)
                return False
            
            # Verify restore success
            if not self._verify_restore_success(new_subvol, snapshot_type):
                # Rollback: delete failed subvol, restore original
                subprocess.run(["btrfs", "subvolume", "delete", new_subvol], capture_output=True)
                subprocess.run(["mv", broken_subvol, current_subvol], capture_output=True)
                return False
            
            # Auto cleanup if enabled
            if self.config.get("auto_cleanup", False):
                subprocess.run(["btrfs", "subvolume", "delete", broken_subvol], 
                             capture_output=True)
            
            return True
        except subprocess.CalledProcessError:
            return False

    def _verify_restore_success(self, restored_subvol: str, snapshot_type: str) -> bool:
        """Verify restored subvolume integrity."""
        if not os.path.exists(restored_subvol):
            return False
        
        # Verify it's a valid btrfs subvolume
        result = subprocess.run(["btrfs", "subvolume", "show", restored_subvol],
                              capture_output=True)
        if result.returncode != 0:
            return False
        
        if snapshot_type == "root":
            for d in ["etc", "usr", "var", "bin"]:
                if not os.path.exists(os.path.join(restored_subvol, d)):
                    return False
            for f in ["etc/fstab", "etc/passwd"]:
                p = os.path.join(restored_subvol, f)
                if not os.path.isfile(p):
                    return False
        elif snapshot_type == "home":
            try:
                if not os.listdir(restored_subvol):
                    return False
            except OSError:
                return False
        else:
            # Any other type: just verify readable
            try:
                os.listdir(restored_subvol)
            except OSError:
                return False
        
        return True

    def purge_old_snapshots(self) -> Tuple[int, List[str]]:
        """Purge old snapshots, keeping only the most recent per type."""
        snapshots_dir = self.config.get("snapshots_dir")
        
        try:
            all_snapshots = []
            for item in os.listdir(snapshots_dir):
                item_path = os.path.join(snapshots_dir, item)
                if os.path.isdir(item_path) and item.startswith('@') and '.' in item:
                    all_snapshots.append(item_path)
            
            if not all_snapshots:
                return 0, []
            
            # Sort snapshots
            all_snapshots.sort()
            
            # Get all unique prefixes dynamically
            prefixes = set()
            for snapshot_path in all_snapshots:
                basename = os.path.basename(snapshot_path)
                if '.' in basename:
                    prefix = basename.split('.')[0]
                    if prefix.startswith('@'):
                        prefixes.add(prefix)
            
            # Find old snapshots to delete for each prefix
            to_delete = []
            for prefix in prefixes:
                type_snapshots = [s for s in all_snapshots 
                                if os.path.basename(s).startswith(f"{prefix}.")]
                
                if len(type_snapshots) > 1:
                    # Keep the last (most recent) one, delete the rest
                    to_delete.extend(type_snapshots[:-1])
            
            if not to_delete:
                return 0, []
            
            # Delete old snapshots
            deleted_count = 0
            deleted_names = []
            for snapshot_path in to_delete:
                try:
                    subprocess.run(["btrfs", "subvolume", "delete", snapshot_path], 
                                 check=True, capture_output=True, text=True)
                    deleted_count += 1
                    deleted_names.append(os.path.basename(snapshot_path))
                except subprocess.CalledProcessError:
                    continue  # Continue with other deletions even if one fails
            
            return deleted_count, deleted_names
            
        except Exception:
            return -1, []  # Error occurred
    
    def clean_broken_subvolumes(self) -> Tuple[int, List[str]]:
        """Clean all .BROKEN subvolumes."""
        btr_pool_dir = self.config.get("btr_pool_dir")
        
        try:
            broken_subvolumes = []
            
            # Find all .BROKEN subvolumes
            for item in os.listdir(btr_pool_dir):
                item_path = os.path.join(btr_pool_dir, item)
                if os.path.isdir(item_path) and ".BROKEN" in item:
                    broken_subvolumes.append(item_path)
            
            if not broken_subvolumes:
                return 0, []
            
            # Delete .BROKEN subvolumes
            deleted_count = 0
            deleted_names = []
            for subvol_path in broken_subvolumes:
                try:
                    subprocess.run(["btrfs", "subvolume", "delete", subvol_path], 
                                 check=True, capture_output=True, text=True)
                    deleted_count += 1
                    deleted_names.append(os.path.basename(subvol_path))
                except subprocess.CalledProcessError:
                    continue  # Continue with other deletions even if one fails
            
            return deleted_count, deleted_names
            
        except Exception:
            return -1, []  # Error occurred

class TUIApp:
    """Main TUI application."""
    
    def __init__(self):
        self.config = Config()
        self.snapshot_manager = SnapshotManager(self.config)
        self.current_screen = "main"
        self.selected_row = 0
        self.selected_col = 0  # 0=root, 1=home, 2=games
        self.status_message = ""
        self.status_timeout = 0
        self.reboot_needed = False  # Track if reboot is needed
    
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
    
    def draw_header(self, stdscr):
        """Draw application header."""
        height, width = stdscr.getmaxyx()
        
        # Title bar
        title = "BTRBK TUI v2.6"
        try:
            stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
            stdscr.addstr(0, 0, title.center(width)[:width-1])
            stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            pass
        
        # Separator - no color, full width
        try:
            stdscr.addstr(1, 0, "-" * width)
        except curses.error:
            pass
    
    def draw_footer(self, stdscr):
        """Draw application footer with key bindings."""
        height, width = stdscr.getmaxyx()
        
        # Key bindings - show H: Reboot when needed
        if self.reboot_needed:
            keys = [
                "Up/Down: Navigate", "Left/Right: Switch", "ENTER: Select", 
                "S: Settings", "R: Refresh", "I: Snapshot", "P: Purge OLD", "B: Clean BROKEN", "H: REBOOT", "Q: Quit"
            ]
        else:
            keys = [
                "Up/Down: Navigate", "Left/Right: Switch", "ENTER: Select", 
                "S: Settings", "R: Refresh", "I: Snapshot", "P: Purge OLD", "B: Clean BROKEN", "Q: Quit"
            ]
        footer_text = " | ".join(keys)
        
        try:
            # Separator - no color, full width
            stdscr.addstr(height - 2, 0, "-" * width)
            # Footer text with color
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(height - 1, 0, footer_text[:width-1].ljust(width-1))
            stdscr.attroff(curses.color_pair(5))
        except curses.error:
            pass
    
    def draw_status(self, stdscr):
        """Draw status message if any."""
        height, width = stdscr.getmaxyx()
        
        # Show temporary status messages first (if active)
        if self.status_message and self.status_timeout > 0:
            try:
                stdscr.attron(curses.color_pair(6))
                stdscr.addstr(height - 3, 0, self.status_message[:width-1].ljust(width-1))
                stdscr.attroff(curses.color_pair(6))
            except curses.error:
                pass
            self.status_timeout -= 1
        elif self.status_timeout <= 0:
            self.status_message = ""
            # Show reboot warning only when no temporary messages are active
            if self.reboot_needed:
                try:
                    stdscr.attron(curses.color_pair(4) | curses.A_BOLD)  # Yellow/Warning color
                    warning_msg = "⚠ REBOOT REQUIRED - Press H to reboot system ⚠"
                    stdscr.addstr(height - 3, 0, warning_msg[:width-1].ljust(width-1))
                    stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
                except curses.error:
                    pass
        elif self.reboot_needed and not self.status_message:
            # Show reboot warning when no temporary messages
            try:
                stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
                warning_msg = "⚠ REBOOT REQUIRED - Press H to reboot system ⚠"
                stdscr.addstr(height - 3, 0, warning_msg[:width-1].ljust(width-1))
                stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            except curses.error:
                pass
    
    def set_status(self, message: str, timeout: int = 30):
        """Set status message with timeout."""
        self.status_message = message
        self.status_timeout = timeout
    
    def create_snapshot(self, stdscr):
        """Create new snapshots using btrbk run --progress."""
        height, width = stdscr.getmaxyx()
        
        # Clear screen and show header
        stdscr.clear()
        self.draw_header(stdscr)
        
        # Show operation title
        title = "Creating Snapshots with btrbk..."
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(4, (width - len(title)) // 2, title)
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        
        # Show instructions
        instruction = "Press ESC to cancel or wait for completion"
        stdscr.attron(curses.A_DIM)
        stdscr.addstr(6, (width - len(instruction)) // 2, instruction)
        stdscr.attroff(curses.A_DIM)
        
        # Simple output area - only horizontal borders
        output_start_y = 8
        output_height = height - 12
        
        # Draw simple horizontal borders
        border = "-" * width
        stdscr.addstr(output_start_y - 1, 0, border)
        stdscr.addstr(output_start_y + output_height, 0, border)
        
        stdscr.refresh()
        
        # Set non-blocking input
        stdscr.nodelay(True)
        
        try:
            # Start btrbk process
            process = subprocess.Popen(
                ["btrbk", "run", "--progress"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                universal_newlines=True,
                bufsize=1
            )
            
            output_lines = []
            current_line = 0
            
            while True:
                # Check for ESC key
                key = stdscr.getch()
                if key == 27:  # ESC
                    process.terminate()
                    process.wait()
                    return False, "Operation cancelled by user"
                
                # Read output from process
                line = process.stdout.readline()
                if line:
                    line = line.rstrip()
                    if line:  # Only add non-empty lines
                        output_lines.append(line)
                        
                        # Display the line in the output area
                        display_y = output_start_y + len(output_lines) - 1 - current_line
                        if display_y >= output_start_y and display_y < output_start_y + output_height:
                            # Truncate line if too long
                            display_line = line[:width] if len(line) > width else line
                            stdscr.addstr(display_y, 0, " " * width)  # Clear line (full width)
                            stdscr.addstr(display_y, 0, display_line)
                        
                        # Auto-scroll if needed
                        if len(output_lines) > output_height:
                            current_line = len(output_lines) - output_height
                        
                        stdscr.refresh()
                
                # Check if process finished
                if process.poll() is not None:
                    break
                
                # Small delay to prevent high CPU usage
                curses.napms(50)
            
            # Get final return code
            return_code = process.returncode
            
            # Show completion message
            if return_code == 0:
                completion_msg = "✓ Snapshots created successfully! Press any key to continue..."
                stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
            else:
                completion_msg = "✗ Error creating snapshots! Press any key to continue..."
                stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            
            stdscr.addstr(height - 2, (width - len(completion_msg)) // 2, completion_msg)
            stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            stdscr.refresh()
            
            # Wait for key press
            stdscr.nodelay(False)
            stdscr.getch()
            
            return return_code == 0, f"btrbk completed with return code {return_code}"
            
        except FileNotFoundError:
            return False, "btrbk command not found"
        except Exception as e:
            return False, f"Error running btrbk: {str(e)}"
        finally:
            # Restore normal input mode
            stdscr.nodelay(False)
        
        try:
            # Get all snapshot directories
            all_snapshots = []
            for item in os.listdir(snapshots_dir):
                item_path = os.path.join(snapshots_dir, item)
                if os.path.isdir(item_path) and (
                    item.startswith("@.") or 
                    item.startswith("@home.") or 
                    item.startswith("@games.")
                ):
                    all_snapshots.append(item_path)
            
            if not all_snapshots:
                return 0, []
            
            # Sort snapshots
            all_snapshots.sort()
            
            # Group by type and find old snapshots to delete
            to_delete = []
            
            def process_type(prefix):
                type_snapshots = [s for s in all_snapshots if os.path.basename(s).startswith(prefix + ".")]
                if len(type_snapshots) > 1:
                    # Keep the last (most recent) one, delete the rest
                    to_delete.extend(type_snapshots[:-1])
            
            process_type("@")
            process_type("@home")
            process_type("@games")
            
            if not to_delete:
                return 0, []
            
            # Delete old snapshots
            deleted_count = 0
            for snapshot_path in to_delete:
                try:
                    result = subprocess.run(
                        ["btrfs", "subvolume", "delete", snapshot_path],
                        capture_output=True, text=True, check=True
                    )
                    deleted_count += 1
                except subprocess.CalledProcessError:
                    pass  # Continue with other snapshots even if one fails
            
            return deleted_count, [os.path.basename(s) for s in to_delete]
            
        except Exception:
            return -1, []  # Error occurred

    def purge_old_snapshots(self) -> Tuple[int, List[str]]:
        """Purge old snapshots using SnapshotManager."""
        return self.snapshot_manager.purge_old_snapshots()
    
    def clean_broken_subvolumes(self) -> Tuple[int, List[str]]:
        """Clean .BROKEN subvolumes using SnapshotManager."""
        return self.snapshot_manager.clean_broken_subvolumes()
    
    def draw_main_screen(self, stdscr):
        """Draw main snapshot selection screen with dynamic columns."""
        height, width = stdscr.getmaxyx()
        
        snapshot_groups, sorted_prefixes = self.snapshot_manager.get_snapshots()
        
        if not snapshot_groups:
            try:
                stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
                stdscr.addstr(height // 2, (width - 20) // 2, "No snapshots found!")
                stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            except curses.error:
                pass
            return
        
        # Ensure selected_col is within bounds
        if self.selected_col >= len(sorted_prefixes):
            self.selected_col = len(sorted_prefixes) - 1
        
        # Calculate column positions dynamically
        num_cols = len(sorted_prefixes)
        col_width = (width - 4) // num_cols if num_cols > 0 else width - 4
        start_y = 4
        
        # Draw column headers
        try:
            stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
            for i, prefix in enumerate(sorted_prefixes):
                col_x = 2 + i * col_width
                snapshots_count = len(snapshot_groups.get(prefix, []))
                header = f"{prefix.upper()} ({snapshots_count})"
                stdscr.addstr(start_y - 1, col_x, header[:col_width-2])
            stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        except curses.error:
            pass
        
        # Draw snapshots for each column
        max_display = height - 8  # Leave space for header/footer
        
        for col_idx, prefix in enumerate(sorted_prefixes):
            snapshots = snapshot_groups.get(prefix, [])
            col_x = 2 + col_idx * col_width
            
            # Ensure selected_row is within bounds for current column
            if col_idx == self.selected_col and self.selected_row >= len(snapshots):
                self.selected_row = max(0, len(snapshots) - 1)
            
            for i, snapshot in enumerate(snapshots[:max_display]):
                if start_y + i >= height - 4:
                    break
                    
                y = start_y + i
                display_name = self.snapshot_manager.format_snapshot_name(snapshot)
                
                try:
                    if self.selected_col == col_idx and i == self.selected_row:
                        stdscr.attron(curses.color_pair(1))
                        stdscr.addstr(y, col_x, display_name[:col_width-2])
                        stdscr.attroff(curses.color_pair(1))
                    else:
                        stdscr.addstr(y, col_x, display_name[:col_width-2])
                except curses.error:
                    pass
        
        # Show current configuration
        config_info = f"Pool: {self.config.get('btr_pool_dir')} | Snapshots: {self.config.get('snapshots_dir')}"
        try:
            stdscr.attron(curses.A_DIM)
            stdscr.addstr(2, 2, config_info[:width-4])
            stdscr.attroff(curses.A_DIM)
        except curses.error:
            pass
    
    def draw_settings_screen(self, stdscr):
        """Draw settings configuration screen."""
        height, width = stdscr.getmaxyx()
        
        settings = [
            ("BTR Pool Directory", "btr_pool_dir"),
            ("Snapshots Directory", "snapshots_dir"),
            ("Auto Cleanup .BROKEN", "auto_cleanup"),
            ("Confirm Actions", "confirm_actions"),
            ("Show Timestamps", "show_timestamps")
        ]
        
        start_y = 4
        
        try:
            stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
            stdscr.addstr(start_y - 1, 4, "SETTINGS")
            stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        except curses.error:
            pass
        
        for i, (label, key) in enumerate(settings):
            if start_y + i * 2 >= height - 8:  # Don't write too close to bottom
                break
                
            y = start_y + i * 2
            value = self.config.get(key)
            
            try:
                if i == self.selected_row:
                    stdscr.attron(curses.color_pair(1))
                
                stdscr.addstr(y, 4, f"{label}:"[:width-6])
                
                if isinstance(value, bool):
                    value_str = "Yes" if value else "No"
                else:
                    value_str = str(value)
                
                stdscr.addstr(y + 1, 6, value_str[:width-8])
                
                if i == self.selected_row:
                    stdscr.attroff(curses.color_pair(1))
            except curses.error:
                pass
        
        # Show config file path and status
        try:
            stdscr.attron(curses.A_DIM)
            config_path = f"Config: {CONFIG_FILE}"
            config_exists = "EXISTS" if CONFIG_FILE.exists() else "NOT FOUND"
            stdscr.addstr(height - 7, 4, f"{config_path} ({config_exists})"[:width-6])
            stdscr.addstr(height - 6, 4, "ENTER: Edit | SPACE: Toggle | ESC: Back | S: Save"[:width-6])
            stdscr.attroff(curses.A_DIM)
        except curses.error:
            pass
    
    def edit_setting(self, stdscr, key: str):
        """Edit a configuration setting."""
        current_value = self.config.get(key)
        
        if isinstance(current_value, bool):
            # Toggle boolean values
            self.config.set(key, not current_value)
            self.config.save()  # Auto-save after change
            self.set_status(f"Toggled {key}")
            return
        
        # Edit string values
        height, width = stdscr.getmaxyx()
        
        # Create edit window
        dialog_width = min(60, width - 8)
        dialog_height = 5
        
        try:
            edit_win = curses.newwin(dialog_height, dialog_width, height // 2 - 2, (width - dialog_width) // 2)
            edit_win.box()
            edit_win.addstr(0, 2, f" Edit {key} "[:dialog_width-4])
            
            # Show current value
            current_str = str(current_value)[:dialog_width-4]
            edit_win.addstr(2, 2, f"Current: {current_str}")
            edit_win.addstr(3, 2, "New: ")
            edit_win.refresh()
            
            # Simple text input
            curses.curs_set(1)
            curses.echo()
            
            try:
                new_value = edit_win.getstr(3, 7, dialog_width - 10).decode('utf-8')
                if new_value.strip():
                    self.config.set(key, new_value.strip())
                    self.config.save()  # Auto-save after change
                    self.set_status(f"Updated {key}")
                else:
                    self.set_status("No changes made")
            except:
                self.set_status("Edit cancelled")
            
            curses.noecho()
            curses.curs_set(0)
            
        except curses.error:
            self.set_status("Cannot create edit dialog")
    
    def confirm_dialog(self, stdscr, message: str) -> bool:
        """Show confirmation dialog."""
        if not self.config.get("confirm_actions", True):
            return True
        
        height, width = stdscr.getmaxyx()
        
        # Create dialog window
        dialog_width = min(len(message) + 10, width - 4)
        dialog_height = 5
        
        try:
            dialog_win = curses.newwin(dialog_height, dialog_width, 
                                     height // 2 - 2, (width - dialog_width) // 2)
            
            dialog_win.box()
            dialog_win.addstr(1, 2, message[:dialog_width - 4])
            dialog_win.addstr(3, 2, "Y: Yes | N: No")
            dialog_win.refresh()
            
            while True:
                key = dialog_win.getch()
                if key in [ord('y'), ord('Y')]:
                    return True
                elif key in [ord('n'), ord('N'), 27]:  # 27 = ESC
                    return False
        except curses.error:
            # If dialog fails, default to confirmation
            return True
    
    def handle_main_input(self, stdscr, key):
        """Handle input for main screen with dynamic columns."""
        snapshot_groups, sorted_prefixes = self.snapshot_manager.get_snapshots()
        
        if not sorted_prefixes:
            return
        
        # Ensure selected_col is within bounds
        if self.selected_col >= len(sorted_prefixes):
            self.selected_col = len(sorted_prefixes) - 1
        
        current_snapshots = snapshot_groups.get(sorted_prefixes[self.selected_col], [])
        
        if key == curses.KEY_UP and self.selected_row > 0:
            self.selected_row -= 1
        elif key == curses.KEY_DOWN:
            if self.selected_row < len(current_snapshots) - 1:
                self.selected_row += 1
        elif key == curses.KEY_LEFT:
            if self.selected_col > 0:
                self.selected_col -= 1
                # Adjust row if new column has fewer items
                new_snapshots = snapshot_groups.get(sorted_prefixes[self.selected_col], [])
                self.selected_row = min(self.selected_row, len(new_snapshots) - 1) if new_snapshots else 0
        elif key == curses.KEY_RIGHT:
            if self.selected_col < len(sorted_prefixes) - 1:
                self.selected_col += 1
                # Adjust row if new column has fewer items
                new_snapshots = snapshot_groups.get(sorted_prefixes[self.selected_col], [])
                self.selected_row = min(self.selected_row, len(new_snapshots) - 1) if new_snapshots else 0
        elif key in [curses.KEY_ENTER, 10, 13]:
            self.handle_snapshot_selection(stdscr, snapshot_groups, sorted_prefixes)
        elif key in [ord('s'), ord('S')]:
            self.current_screen = "settings"
            self.selected_row = 0
        elif key in [ord('r'), ord('R')]:
            # Always refresh
            self.set_status("Refreshed snapshot list")
        elif key in [ord('h'), ord('H')]:
            # Reboot if needed
            if self.reboot_needed:
                if self.confirm_dialog(stdscr, "Reboot system now?"):
                    subprocess.run(["reboot"], capture_output=True)
                else:
                    self.set_status("Reboot cancelled")
            else:
                self.set_status("No reboot needed")
        elif key in [ord('p'), ord('P')]:
            if self.confirm_dialog(stdscr, "Purge old snapshots (keep only most recent)?"):
                self.set_status("Purging old snapshots...", 30)
                stdscr.refresh()
                
                deleted_count, deleted_list = self.purge_old_snapshots()
                
                if deleted_count == -1:
                    self.set_status("Error: cannot read snapshots directory", 100)
                elif deleted_count == 0:
                    self.set_status("No old snapshots to purge", 50)
                else:
                    self.set_status(f"Purged {deleted_count} old snapshots successfully", 150)
            else:
                self.set_status("Purge cancelled")
        elif key in [ord('b'), ord('B')]:
            if self.confirm_dialog(stdscr, "Delete all .BROKEN subvolumes?"):
                self.set_status("Cleaning .BROKEN subvolumes...", 30)
                stdscr.refresh()
                
                deleted_count, deleted_list = self.clean_broken_subvolumes()
                
                if deleted_count == -1:
                    self.set_status("Error: cannot read pool directory", 100)
                elif deleted_count == 0:
                    self.set_status("No .BROKEN subvolumes found", 50)
                else:
                    self.set_status(f"Cleaned {deleted_count} .BROKEN subvolumes successfully", 150)
            else:
                self.set_status("Clean cancelled")
        elif key in [ord('i'), ord('I')]:
            if self.confirm_dialog(stdscr, "Create new snapshots with btrbk?"):
                success, message = self.create_snapshot(stdscr)
                if success:
                    self.set_status("Snapshots created successfully", 100)
                else:
                    self.set_status(f"Snapshot creation failed: {message}", 150)
            else:
                self.set_status("Snapshot creation cancelled")
    
    def handle_snapshot_selection(self, stdscr, snapshot_groups, sorted_prefixes):
        """Handle snapshot selection and restoration with dynamic columns."""
        if not sorted_prefixes or self.selected_col >= len(sorted_prefixes):
            return
        
        current_prefix = sorted_prefixes[self.selected_col]
        current_snapshots = snapshot_groups.get(current_prefix, [])
        
        if not current_snapshots or self.selected_row >= len(current_snapshots):
            return
        
        snapshot = current_snapshots[self.selected_row]
        # Extract snapshot type from prefix
        if current_prefix == "@":
            snapshot_type = "root"  # Special case for root subvolume
        elif current_prefix.startswith('@'):
            snapshot_type = current_prefix[1:]  # Remove @ prefix for others
        else:
            snapshot_type = current_prefix
        
        # Confirm restoration
        if not self.confirm_dialog(stdscr, f"Restore {snapshot_type} snapshot?"):
            self.set_status("Restoration cancelled")
            return
        
        # Perform restoration
        self.set_status("Restoring snapshot...", 30)
        stdscr.refresh()
        
        if self.snapshot_manager.restore_snapshot(snapshot, snapshot_type):
            self.reboot_needed = True
            self.set_status(f"{snapshot_type} snapshot restored! Press H to reboot when ready", 150)
        else:
            self.set_status(f"Error: {snapshot_type} snapshot restore failed (rolled back)", 150)
    
    def handle_settings_input(self, stdscr, key):
        """Handle input for settings screen."""
        settings_count = 5  # Number of settings
        
        if key == curses.KEY_UP and self.selected_row > 0:
            self.selected_row -= 1
        elif key == curses.KEY_DOWN and self.selected_row < settings_count - 1:
            self.selected_row += 1
        elif key in [curses.KEY_ENTER, 10, 13]:
            settings_keys = ["btr_pool_dir", "snapshots_dir", "auto_cleanup", 
                           "confirm_actions", "show_timestamps"]
            self.edit_setting(stdscr, settings_keys[self.selected_row])
        elif key == ord(' '):  # Space to toggle boolean values
            settings_keys = ["btr_pool_dir", "snapshots_dir", "auto_cleanup", 
                           "confirm_actions", "show_timestamps"]
            key_name = settings_keys[self.selected_row]
            if isinstance(self.config.get(key_name), bool):
                self.config.set(key_name, not self.config.get(key_name))
                self.config.save()  # Auto-save after toggle
                self.set_status(f"Toggled {key_name}")
        elif key in [ord('s'), ord('S')]:
            # Manual save (though auto-save is already active)
            self.config.save()
            self.set_status("Settings saved manually!")
        elif key == 27:  # ESC
            self.current_screen = "main"
            self.selected_row = 0
    
    def run(self, stdscr):
        """Main application loop."""
        curses.curs_set(0)
        stdscr.timeout(100)  # Non-blocking input with timeout
        
        self.init_colors()
        
        while True:
            stdscr.clear()
            
            # Draw UI components
            self.draw_header(stdscr)
            
            if self.current_screen == "main":
                self.draw_main_screen(stdscr)
            elif self.current_screen == "settings":
                self.draw_settings_screen(stdscr)
            
            self.draw_status(stdscr)
            self.draw_footer(stdscr)
            
            stdscr.refresh()
            
            # Handle input
            key = stdscr.getch()
            
            if key == -1:  # Timeout, continue loop
                continue
            elif key in [ord('q'), ord('Q')]:
                break
            elif self.current_screen == "main":
                self.handle_main_input(stdscr, key)
            elif self.current_screen == "settings":
                self.handle_settings_input(stdscr, key)

def main():
    """Main entry point."""
    if os.geteuid() != 0:
        print("Error: This tool requires root privileges.")
        print("Please run with sudo.")
        sys.exit(1)
    
    try:
        app = TUIApp()
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
