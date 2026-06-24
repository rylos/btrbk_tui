#!/usr/bin/python
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

# Configura le cartelle (default; sovrascritte dalla config condivisa se presente)
btr_pool_dir = "/mnt/btr_pool"
snapshots_dir = "/mnt/btr_pool/btrbk_snapshots"

# File di configurazione condiviso con le versioni TUI
CONFIG_FILE = Path.home() / ".config" / "btrbk_tui" / "config.json"

def load_config():
    """Carica btr_pool_dir e snapshots_dir dalla config condivisa, se presente."""
    global btr_pool_dir, snapshots_dir
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
            btr_pool_dir = data.get("btr_pool_dir", btr_pool_dir)
            snapshots_dir = data.get("snapshots_dir", snapshots_dir)
    except Exception:
        pass  # In caso di config corrotta si usano i default

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

def verify_restore_success(target_path, prefix):
    """Verifica l'integrità del subvolume ripristinato (coerente con le TUI)."""
    if not os.path.exists(target_path):
        return False

    # Deve essere un subvolume btrfs valido
    if subprocess.run(["btrfs", "subvolume", "show", target_path],
                      capture_output=True).returncode != 0:
        return False

    if prefix == '@':
        for d in ["etc", "usr", "var", "bin"]:
            if not os.path.exists(os.path.join(target_path, d)):
                return False
        for f in ["etc/fstab", "etc/passwd"]:
            if not os.path.isfile(os.path.join(target_path, f)):
                return False
    elif prefix == '@home':
        try:
            if not os.listdir(target_path):
                return False
        except OSError:
            return False
    else:
        # Qualsiasi altro tipo (@games, @work, ecc.): basta che sia leggibile
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

    print(f"\nRipristino snapshot: {selected_snapshot}")
    print(f"Tipo: {prefix}")
    print(f"Da: {source_path}")
    print(f"A: {target_path}")

    # Pre-check: lo snapshot sorgente deve esistere
    if not os.path.exists(source_path):
        print(f"❌ Errore: snapshot sorgente non trovato: {source_path}")
        exit(1)

    current_existed = os.path.exists(target_path)

    # Guardia: il subvolume corrente deve essere un vero subvolume btrfs
    if current_existed:
        if subprocess.run(["btrfs", "subvolume", "show", target_path],
                          capture_output=True).returncode != 0:
            print(f"❌ Errore: {target_path} non è un subvolume btrfs valido. Operazione annullata.")
            exit(1)

        print(f"Spostamento {target_path} -> {broken_path}")
        if subprocess.run(["mv", "--verbose", target_path, broken_path]).returncode != 0:
            print("❌ Errore: impossibile spostare il subvolume corrente. Operazione annullata.")
            exit(1)

    # Create snapshot
    print("Creazione snapshot...")
    if subprocess.run(["btrfs", "subvolume", "snapshot", source_path, target_path]).returncode != 0:
        print("❌ Errore: creazione snapshot fallita. Rollback in corso...")
        if current_existed:
            if subprocess.run(["mv", broken_path, target_path]).returncode != 0:
                print(f"🔥 CRITICO: rollback fallito! Stato incoerente. Recupero manuale: {broken_path}")
                exit(1)
            print("↩️  Rollback completato: stato precedente ripristinato.")
        exit(1)

    # Verify restore success
    if not verify_restore_success(target_path, prefix):
        print("❌ Errore: verifica integrità fallita. Rollback in corso...")
        if subprocess.run(["btrfs", "subvolume", "delete", target_path]).returncode != 0:
            print(f"🔥 CRITICO: impossibile rimuovere il subvolume fallito! Recupero manuale: {broken_path}")
            exit(1)
        if current_existed:
            if subprocess.run(["mv", broken_path, target_path]).returncode != 0:
                print(f"🔥 CRITICO: rollback fallito! Stato incoerente. Recupero manuale: {broken_path}")
                exit(1)
            print("↩️  Rollback completato: stato precedente ripristinato.")
        exit(1)

    print("✅ Snapshot ripristinato con successo!")

    # Ask about removing .BROKEN
    if current_existed and os.path.exists(broken_path):
        do_remove_broken = input(f"\nVuoi eliminare lo snapshot {subvolume_name}.BROKEN? (s/n): ")
        if do_remove_broken.lower() == 's':
            print(f"Eliminazione {broken_path}...")
            if subprocess.run(["btrfs", "subvolume", "delete", broken_path]).returncode == 0:
                print("✅ Snapshot .BROKEN eliminato!")
            else:
                print("⚠️  Impossibile eliminare lo snapshot .BROKEN.")

    # Ask about reboot
    do_reboot = input("\nVuoi riavviare il sistema? (s/n): ")
    if do_reboot.lower() == 's':
        print("Riavvio del sistema...")
        subprocess.run(["sync"])
        subprocess.run(["reboot"])

def main():
    """Main function."""
    # Le operazioni btrfs richiedono privilegi di root
    if os.geteuid() != 0:
        print("❌ Errore: questo strumento richiede privilegi di root. Esegui con sudo.")
        exit(1)

    print("🔄 BTRBK TUI v2.6 - Versione CLI Dinamica")
    print("=" * 50)

    # Carica la configurazione condivisa con le versioni TUI
    load_config()

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

