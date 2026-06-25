# Spec: Kiosk Connection UI Refactoring

## Goal
Resolve a logical deadlock on kiosk boot: when the kiosk is not connected to the orchestrator, it shows a full-screen blocking modal overlay. This blocks the user from accessing network settings (Wi-Fi/Ethernet) and switching to offline restore mode.

The solution is to display the standard application header and footer unconditionally on boot, allowing full access to configuration settings, and relocate the connection/pairing screen to a non-blocking connection card at the bottom of the page.

---

## Proposed Changes

### App Shell & Boot Flow (`App.tsx`)
1. **Unconditional Render**: Remove the conditional `return <BlockedKioskScreen ... />` at the root layout block.
2. **Tab Access**: Allow navigation tabs to be clicked.
3. **Kiosk Connection Panel Location**: Render the connection card (refactored `KioskConnectionPanel` or modified `BlockedKioskScreen`) inside the main page layout above the `Footer` element.
4. **Conditional Display**: The panel will only render when:
   - `isKiosk` is `true`
   - `restoreMode` is `'online'`
   - `kioskStatus` is not `'APPROVED'` (e.g., `PENDING`, `DISABLED`, `REVOKED`, or unconnected).

### Connection Card Component (`BlockedKioskScreen` -> `KioskConnectionPanel`)
1. **Layout**: Convert the full-screen layout to a bottom-anchored, compact card layout:
   - Width: `w-full max-w-lg mx-auto`
   - Container: `bg-zinc-900/80 border border-zinc-800/85 backdrop-blur-md rounded-2xl p-6 shadow-2xl space-y-4`
   - Positioning: Rendered directly below `<main>` or inside `<main>` but below active tab content.
2. **Interactions**: Keep the request activation button, status text, and the server pairing form intact.

### Flasher Tab (`FlasherTab.tsx`)
1. **Online Non-Approved Guard**: If `isKiosk` is `true`, `restoreMode` is `'online'`, and `kioskStatus !== 'APPROVED'`:
   - Display a centered alert/warning banner inside the tab content: *"Waiting for Orchestrator server connection. Please link this kiosk to the server or switch to Offline Mode."*
   - Blur or disable interactive elements (device scanning, file selection, flash triggers) under this condition.

---

## Verification Plan

### Manual Verification
1. Boot the kiosk without active network configurations.
2. Verify the header is visible and the network configuration settings modal can be opened and customized.
3. Verify the mode toggle allows switching to "Offline Mode" and back to "Online Mode".
4. When in "Offline Mode", verify that the bottom connection card is hidden and the Flasher is fully active (using local USB cache).
5. When in "Online Mode" and not approved, verify that the Flasher dashboard displays a warning banner and blocks interaction, while showing the pairing/connection card at the bottom.
6. Verify successful pairing removes the connection card and unblocks the Flasher tab.
