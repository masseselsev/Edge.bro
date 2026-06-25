# Design Spec: Flasher Disk Mismatch Resolution

Resolve the issue where the "Start Flashing" button is permanently disabled due to a false disk type mismatch warning and a missing override confirmation checkbox in the UI.

## Problem Description
1. **False Mismatch**: The orchestrator database stores node disk types as full hardware descriptions (e.g. `"SATA 232.9G Samsung SSD 870 EVO 250GB"`). When compared directly with target block device types (which are strictly `"SATA"` or `"NVME"`), it causes a false disk type mismatch warning.
2. **Missing UI Checkbox**: The UI lacks the checkbox to confirm and set `overrideChecked` to `true`, making it impossible to bypass the warning and enable the button.
3. **Double Slash**: The UI warning card displays `/dev//dev/sdb` due to a duplicate prefix in the translation lookup.

## Proposed Changes

### 1. Base Disk Type Parsing (Backend & Frontend)
Extract the base disk type (`SATA` or `NVME`) from the detailed node disk type string by checking its prefix:
- **Backend** ([restore.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/restore.py)):
  ```python
  node_base_type = "NVME" if node.disk_type.upper().startswith("NVME") else ("SATA" if node.disk_type.upper().startswith("SATA") else "UNKNOWN")
  ```
- **Frontend** ([FlasherTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FlasherTab.tsx)):
  ```typescript
  const nodeBaseType = node.disk_type.toUpperCase().startsWith('NVME') ? 'NVME' : (node.disk_type.toUpperCase().startsWith('SATA') ? 'SATA' : 'UNKNOWN');
  const isMismatch = nodeBaseType !== 'UNKNOWN' && nodeBaseType !== device.disk_type;
  ```

### 2. Confirm Checkbox in Warning Card (Frontend)
Render a checkbox in the amber warning box in [FlasherTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FlasherTab.tsx) that allows users to explicitly toggle `overrideChecked`.

### 3. Fix Warning Text Prefix (Frontend)
Strip the `/dev/` prefix when passing `selectedDevice` to the mismatch warning translation:
```typescript
t('flashWarningText', { dev: selectedDevice.replace(/^\/dev\//, ''), ... })
```

## Verification Plan
1. **Unit Tests**:
   - Run existing backend tests to ensure no regressions.
2. **Manual Verification**:
   - Verify mismatch warning does not show up when a node with `"SATA..."` disk type is flashed to a `"SATA"` target device.
   - Verify that when a genuine mismatch exists, the warning card shows the override checkbox, and checking it enables the "Start Flashing" button.
