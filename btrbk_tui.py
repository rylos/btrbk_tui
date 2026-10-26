#!/usr/bin/python
import os
import subprocess
import sys
from datetime import datetime

# Snapshot naming, timestamps, config lookup and mount detection are shared
# with the TUI, so that the three versions agree on all of them
from btrbk_tui_pro import (
    VERSION,
    Config,
    group_snapshots,
    mounted_subvolume,
    parse_btrbk_timestamp,
    split_snapshot_name,
)

# Directories (defaults; overridden by the shared config if present)
btr_pool_dir = "/mnt/btr_pool"
snapshots_dir = "/mnt/btr_pool/btrbk_snapshots"

def load_config():
    """Load btr_pool_dir and snapshots_dir from the config shared with the TUIs.

    Same lookup as the TUIs: under sudo the config of the user who ran sudo,
    then root's, then the old ~/.config/btrbk_restore path.
    """
    global btr_pool_dir, snapshots_dir
    config = Config()
    btr_pool_dir = config.get("btr_pool_dir", btr_pool_dir)
    snapshots_dir = config.get("snapshots_dir", snapshots_dir)

def get_snapshot_groups():
    """Get snapshots grouped by subvolume, "@" first, newest first."""
    try:
        names = [item for item in os.listdir(snapshots_dir)
                 if os.path.isdir(os.path.join(snapshots_dir, item))]
    except FileNotFoundError:
        print(f"Error: Directory {snapshots_dir} not found!")
        sys.exit(1)
    except OSError as e:
        print(f"Error reading snapshots: {e}")
        sys.exit(1)
    return group_snapshots(names)

def format_snapshot_name(snapshot):
    """Format snapshot name with timestamp (optional)."""
    parts = split_snapshot_name(snapshot)
    dt = parse_btrbk_timestamp(parts[1]) if parts else None
    return f"{snapshot} ({dt.strftime('%Y-%m-%d %H:%M:%S')})" if dt else snapshot

def display_snapshots(snapshot_groups):
    """Display snapshots organized by groups."""
    if not snapshot_groups:
        print("No snapshots found!")
        sys.exit(1)

    print("Available snapshots:\n")
    print("0. Exit")

    snapshot_list = []
    counter = 1

    for prefix, snapshots in snapshot_groups:
        print(f"\n--- {prefix.upper()} ({len(snapshots)} snapshots) ---")

        for snapshot in snapshots:
            formatted_name = format_snapshot_name(snapshot)
            print(f"{counter}. {formatted_name}")
            snapshot_list.append((snapshot, prefix))
            counter += 1

    return snapshot_list

def verify_restore_success(target_path, prefix):
    """Check the integrity of the restored subvolume (consistent with the TUIs)."""
    if not os.path.exists(target_path):
        return False

    # Must be a valid btrfs subvolume
    if subprocess.run(["btrfs", "subvolume", "show", target_path],
                      capture_output=True).returncode != 0:
        return False

    # Root and home are recognised by the name "@"/"@home" or by what is mounted
    # on / and /home: layouts without "@" call them root, rootfs, home...
    try:
        with open("/proc/self/mountinfo") as f:
            mountinfo = f.read()
    except OSError:
        mountinfo = ""
    is_root = prefix == '@' or mounted_subvolume(mountinfo, "/") == prefix
    is_home = prefix == '@home' or mounted_subvolume(mountinfo, "/home") == prefix

    if is_root:
        for d in ["etc", "usr", "var", "bin"]:
            if not os.path.exists(os.path.join(target_path, d)):
                return False
        for f in ["etc/fstab", "etc/passwd"]:
            if not os.path.isfile(os.path.join(target_path, f)):
                return False
    elif is_home:
        try:
            if not os.listdir(target_path):
                return False
        except OSError:
            return False
    else:
        # Any other type (@games, @work, etc.): readable is enough
        try:
            os.listdir(target_path)
        except OSError:
            return False

    return True

def restore_snapshot(selected_snapshot, prefix):
    """Restore the selected snapshot with verification and rollback."""
    source_path = os.path.join(snapshots_dir, selected_snapshot)

    # Determine the target subvolume name (consistent with TUI versions)
    subvolume_name = prefix  # prefix already includes '@' (@, @home, @games, ...)

    target_path = os.path.join(btr_pool_dir, subvolume_name)
    # Generate unique .BROKEN name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    broken_path = f"{target_path}.BROKEN.{timestamp}"

    print(f"\nRestoring snapshot: {selected_snapshot}")
    print(f"Type: {prefix}")
    print(f"From: {source_path}")
    print(f"To: {target_path}")

    # Pre-check: the source snapshot must exist
    if not os.path.exists(source_path):
        print(f"❌ Error: source snapshot not found: {source_path}")
        sys.exit(1)

    current_existed = os.path.exists(target_path)

    # Guard: the current subvolume must be a real btrfs subvolume
    if current_existed:
        if subprocess.run(["btrfs", "subvolume", "show", target_path],
                          capture_output=True).returncode != 0:
            print(f"❌ Error: {target_path} is not a valid btrfs subvolume. Operation cancelled.")
            sys.exit(1)

        print(f"Moving {target_path} -> {broken_path}")
        if subprocess.run(["mv", "--verbose", target_path, broken_path]).returncode != 0:
            print("❌ Error: cannot move the current subvolume. Operation cancelled.")
            sys.exit(1)

    # Create snapshot
    print("Creating snapshot...")
    if subprocess.run(["btrfs", "subvolume", "snapshot", source_path, target_path]).returncode != 0:
        print("❌ Error: snapshot creation failed. Rolling back...")
        if current_existed:
            if subprocess.run(["mv", broken_path, target_path]).returncode != 0:
                print(f"🔥 CRITICAL: rollback failed! Inconsistent state. Manual recovery: {broken_path}")
                sys.exit(1)
            print("↩️  Rollback complete: previous state restored.")
        sys.exit(1)

    # Verify restore success
    if not verify_restore_success(target_path, prefix):
        print("❌ Error: integrity check failed. Rolling back...")
        if subprocess.run(["btrfs", "subvolume", "delete", target_path]).returncode != 0:
            print(f"🔥 CRITICAL: cannot remove the failed subvolume! Manual recovery: {broken_path}")
            sys.exit(1)
        if current_existed:
            if subprocess.run(["mv", broken_path, target_path]).returncode != 0:
                print(f"🔥 CRITICAL: rollback failed! Inconsistent state. Manual recovery: {broken_path}")
                sys.exit(1)
            print("↩️  Rollback complete: previous state restored.")
        sys.exit(1)

    print("✅ Snapshot restored successfully!")

    # Ask about removing .BROKEN
    if current_existed and os.path.exists(broken_path):
        do_remove_broken = input(f"\nDelete the {subvolume_name}.BROKEN snapshot? (y/n): ")
        if do_remove_broken.lower() == 'y':
            print(f"Deleting {broken_path}...")
            if subprocess.run(["btrfs", "subvolume", "delete", broken_path]).returncode == 0:
                print("✅ .BROKEN snapshot deleted!")
            else:
                print("⚠️  Could not delete the .BROKEN snapshot.")

    # Ask about reboot
    do_reboot = input("\nReboot the system? (y/n): ")
    if do_reboot.lower() == 'y':
        print("Rebooting the system...")
        subprocess.run(["sync"])
        subprocess.run(["reboot"])

def main():
    """Main function."""
    # btrfs operations need root privileges
    if os.geteuid() != 0:
        print("❌ Error: this tool needs root privileges. Run it with sudo.")
        sys.exit(1)

    print(f"🔄 BTRBK TUI v{VERSION} - Dynamic CLI Version")
    print("=" * 50)

    # Load the config shared with the TUI versions
    load_config()

    # Get snapshot groups dynamically
    snapshot_groups = get_snapshot_groups()

    # Display snapshots
    snapshot_list = display_snapshots(snapshot_groups)

    # Get user choice
    try:
        choice = int(input(f"\nChoose the snapshot to restore (0-{len(snapshot_list)}): "))
    except ValueError:
        print("❌ Invalid choice. Enter a number.")
        sys.exit(1)

    # Exit if 0
    if choice == 0:
        print("👋 Exiting...")
        sys.exit(0)

    # Validate choice
    if 1 <= choice <= len(snapshot_list):
        selected_snapshot, prefix = snapshot_list[choice - 1]
        restore_snapshot(selected_snapshot, prefix)
    else:
        print("❌ Invalid choice.")
        sys.exit(1)

if __name__ == "__main__":
    main()

