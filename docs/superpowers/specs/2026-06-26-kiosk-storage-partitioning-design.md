# Design Spec: Kiosk Storage Partitioning Fix

Optimize and patch the kiosk initial boot partitioning script to handle GPT backup header relocation errors and ensure consistent unit parsing.

## Problem Description
1. **GPT Backup Header Mismatch**: When a raw live ISO image is written to a larger USB drive (e.g. 3.5GB ISO written to 32GB drive), the backup GPT header remains located in the middle of the physical disk. When [kiosk-storage-setup.sh](file:///home/masse/projects/Backup-edge-Restore/payload_client/kiosk-storage-setup.sh) runs `parted -s print` to print partitions, the script fails with exit code `1` due to `-s` (script mode) treating warnings as fatal errors. Because of `set -e`, the script immediately exits without creating the persistent partition.
2. **Inconsistent Unit Parsing**: Finding the end of the last partition using `parted print` without specifying units can return values in auto-scaled units like `GB`, `MB`, or `B`, leading to parsing inconsistencies.

## Proposed Changes

### Relocate GPT Headers and Standardize Units
Update [kiosk-storage-setup.sh](file:///home/masse/projects/Backup-edge-Restore/payload_client/kiosk-storage-setup.sh):
1. Replace `parted -s "$PARENT_DISK" print` with `echo "Fix" | parted "$PARENT_DISK" print || true` to relocate GPT backup headers to the physical end of the disk non-interactively.
2. Update the `LAST_END` calculation to force `unit MB` in `parted`, ensuring a standard and consistent output unit (e.g. `3028MB`).

```bash
    # Relocate GPT backup headers to the physical end of the disk
    echo "Fix" | parted "$PARENT_DISK" print || true
    
    # Find end of last partition in MB
    LAST_END=$(parted -s "$PARENT_DISK" unit MB print | awk '/^[0-9]/ {end=$3} END {print end}')
```

## Verification Plan
1. **Script Validation**:
   - Verify script syntax via shellcheck or local bash parsing.
2. **Manual Verification**:
   - Pack the kiosk ISO and flash it onto a USB drive.
   - Boot a dynamic kiosk and confirm `kiosk-data` partition is successfully created at the physical end of the drive.
