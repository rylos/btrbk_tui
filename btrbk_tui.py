#!/usr/bin/python
import os
import subprocess
from datetime import datetime

# Configura le cartelle
btr_pool_dir = "/mnt/btr_pool"
snapshots_dir = "/mnt/btr_pool/btrbk_snapshots"

def get_snapshot_groups():
    """Get snapshots organized by type (dynamically detected)."""
    try:
        # Get all snapshot directories and extract prefixes
        all_items = os.listdir(snapshots_dir)
        snapshot_groups = {}
        
        for item in all_items:
            item_path = os.path.join(snapshots_dir, item)
            if os.path.isdir(item_path):
                # Extract prefix (everything before the first dot)
                if '.' in item:
                    prefix = item.split('.')[0]
                    if prefix.startswith('@'):  # Only consider btrfs subvolumes
                        if prefix not in snapshot_groups:
                            snapshot_groups[prefix] = []
                        snapshot_groups[prefix].append(item)
        
        # Sort each group by name (which corresponds to timestamp)
        for prefix in snapshot_groups:
            snapshot_groups[prefix].sort(reverse=True)  # Newest first
        
        return snapshot_groups
        
    except FileNotFoundError:
        print(f"Error: Directory {snapshots_dir} not found!")
        exit(1)
    except Exception as e:
        print(f"Error reading snapshots: {e}")
        exit(1)

def format_snapshot_name(snapshot):
    """Format snapshot name with timestamp (optional)."""
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

def display_snapshots(snapshot_groups):
    """Display snapshots organized by groups."""
    if not snapshot_groups:
        print("No snapshots found!")
        exit(1)
    
    print("Lista degli snapshot disponibili:\n")
    print("0. Esci")
    
    snapshot_list = []
    counter = 1
    
    # Sort prefixes for consistent ordering (@ first, then alphabetically)
    sorted_prefixes = sorted(snapshot_groups.keys(), key=lambda x: (x != '@', x))
    
    for prefix in sorted_prefixes:
        snapshots = snapshot_groups[prefix]
        print(f"\n--- {prefix.upper()} ({len(snapshots)} snapshots) ---")
        
        for snapshot in snapshots:
            formatted_name = format_snapshot_name(snapshot)
            print(f"{counter}. {formatted_name}")
            snapshot_list.append((snapshot, prefix))
            counter += 1
    
    return snapshot_list

def restore_snapshot(selected_snapshot, prefix):
    """Restore the selected snapshot."""
    source_path = os.path.join(snapshots_dir, selected_snapshot)
    
    # Determine the target subvolume name (consistent with TUI versions)
    if prefix == '@':
        subvolume_name = '@'
    else:
        subvolume_name = prefix  # Keep full prefix (@home, @games, @custom, etc.)
    
    target_path = os.path.join(btr_pool_dir, subvolume_name)
    # Generate unique .BROKEN name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    broken_path = f"{target_path}.BROKEN.{timestamp}"
    
    print(f"\nRipristino snapshot: {selected_snapshot}")
    print(f"Tipo: {prefix}")
    print(f"Da: {source_path}")
    print(f"A: {target_path}")
    
    try:
        # Move existing subvolume to .BROKEN
        if os.path.exists(target_path):
            print(f"Spostamento {target_path} -> {broken_path}")
            subprocess.run(["mv", "--verbose", target_path, broken_path], check=True)
        
        # Create snapshot
        print(f"Creazione snapshot...")
        subprocess.run(["btrfs", "subvolume", "snapshot", source_path, target_path], check=True)
        
        print(f"✅ Snapshot ripristinato con successo!")
        
        # Ask about removing .BROKEN
        if os.path.exists(broken_path):
            do_remove_broken = input(f"\nVuoi eliminare lo snapshot {subvolume_name}.BROKEN? (s/n): ")
            if do_remove_broken.lower() == 's':
                print(f"Eliminazione {broken_path}...")
                subprocess.run(["btrfs", "subvolume", "delete", broken_path], check=True)
                print("✅ Snapshot .BROKEN eliminato!")
        
        # Ask about reboot
        do_reboot = input("\nVuoi riavviare il sistema? (s/n): ")
        if do_reboot.lower() == 's':
            print("Riavvio del sistema...")
            subprocess.run(["reboot"])
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Errore durante il ripristino: {e}")
        exit(1)
    except Exception as e:
        print(f"❌ Errore: {e}")
        exit(1)

def main():
    """Main function."""
    print("🔄 BTRBK TUI v2.6 - Versione CLI Dinamica")
    print("=" * 50)
    
    # Get snapshot groups dynamically
    snapshot_groups = get_snapshot_groups()
    
    # Display snapshots
    snapshot_list = display_snapshots(snapshot_groups)
    
    # Get user choice
    try:
        choice = int(input(f"\nScegli lo snapshot da ripristinare (0-{len(snapshot_list)}): "))
    except ValueError:
        print("❌ Scelta non valida. Inserire un numero.")
        exit(1)
    
    # Exit if 0
    if choice == 0:
        print("👋 Uscita...")
        exit(0)
    
    # Validate choice
    if 1 <= choice <= len(snapshot_list):
        selected_snapshot, prefix = snapshot_list[choice - 1]
        restore_snapshot(selected_snapshot, prefix)
    else:
        print("❌ Scelta non valida.")
        exit(1)

if __name__ == "__main__":
    main()

