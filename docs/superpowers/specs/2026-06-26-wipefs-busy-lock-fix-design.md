# Design Spec: wipefs Busy Lock Resolution

Resolve the restore task execution failure caused by target devices (or their partitions) being locked or mounted by the host system during the `wipefs -a` stage.

## Problem Description
1. **wipefs Failure**: During bare-metal restore flashing, the Celery worker task runs:
   ```python
   subprocess.check_call(["wipefs", "-a", target_dev])
   ```
   If any partition of the target block device (e.g. `/dev/sdb1`, `/dev/sdb2`) is mounted by the host OS or local system, `wipefs` fails with exit code `1` due to "device or resource busy" locks.
2. **Missing Auto-Unmount**: The orchestrator currently does not check for or release active mount locks on the target device before attempting to wipe and partition it.

## Proposed Changes

### Release Mount Locks Before Wiping
In [disk_ops.py](file:///home/masse/projects/Backup-edge-Restore/backend/core/disk_ops.py), before running `wipefs`, read `/proc/mounts` and perform a lazy unmount (`umount -l`) on any mounts associated with the target device or its partitions:

```python
        # 1.5. Release active mount locks on target device & its partitions
        try:
            import re
            if os.path.exists("/proc/mounts"):
                # Matches target_dev itself or target_dev followed by partition numbers (e.g. /dev/sdb1, /dev/nvme0n1p2)
                part_pattern = re.compile(r"^" + re.escape(target_dev) + r"(p?\d+)?$")
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            dev_src = parts[0]
                            if part_pattern.match(dev_src):
                                mount_point = parts[1]
                                emit_log(f"Releasing mount lock: unmounting {dev_src} from {mount_point}...", prog=8)
                                subprocess.call(["umount", "-l", mount_point])
        except Exception as ue:
            emit_log(f"Warning: Failed to release mount locks: {str(ue)}")
```

## Verification Plan
1. **Unit Tests**:
   - Add a test case to `backend/tests/test_restore.py` verifying that the logic correctly identifies and unmounts mounted partitions.
2. **Manual Verification**:
   - Trigger a disk restore onto a USB drive that has actively mounted partitions, and verify it successfully unmounts them and proceeds with flashing.
