# Design Spec: Edge B.R.O. Rebranding and Header Refactoring

Rebrand the application from "Borg Backup & Restore Orchestrator" to **Edge B.R.O. (Edge Backup & Restore Orchestrator)**, remove the redundant static "System Online" indicator, adjust the header layout to a spacious two-row design, and replace the old status indicator with a dynamic header language dropdown switcher.

## Proposed Changes

### Brand Identity & Logo
- **Brand Name:** "Edge B.R.O." (short/acronym format) which stands for "Edge Backup & Restore Orchestrator".
- **Compact Header Logo:** A stylized SVG shield icon containing a storage disk/node graphic with cyan/purple gradient lines, an outer glowing filter, and a small active orange activity dot.
- **Hero Welcome Logo:** Embed `/content/edge_bro_logo.png` (generated tech illustration) into the welcome/fleet tab when no edge nodes are registered yet, enhancing the dashboard's visual style.

### UI Layout & Components
- **Two-Row Header (App.tsx):**
  - **Row 1:** 
    - **Left:** SVG shield logo icon, text title "Edge B.R.O." (inside a highlighted capsule/badge), full subtitle "Edge Backup & Restore Orchestrator" (supporting multilingual versions), and version badge.
    - **Right:** Kiosk mode actions and status buttons, plus a new dropdown language selector.
  - **Row 2:** 
    - Full width tab navigation buttons (`Fleet`, `Schedule`, `Flasher`, `USB Generator`, `History`, `Logs`, `Settings`), cleanly separated from Row 1 by a thin horizontal border.
- **Language Switcher Dropdown:**
  - Placed in the top-right corner of Row 1 in place of the old "System Online" indicator.
  - Displays the currently selected language code (e.g., `EN`, `RU`, `UK`) with a mini-globe icon.
  - Clicking/hovering on the button reveals a dropdown menu displaying the other available languages.
  - Uses CSS transition animation class (`animate-dropdown-in`) for premium look-and-feel.
  - Implements click-outside-to-close behavior.
- **Settings tab (SettingsTab.tsx):**
  - Remove the duplicate application language selection dropdown list since it is now accessible globally in the header.

### Translations & Localization (translations.ts)
- Update all occurrences of the old application name and description in EN, RU, and UK localization maps:
  - **English:**
    - App Name: `Edge B.R.O.`
    - Subtitle: `Edge Backup & Restore Orchestrator`
    - Kiosk Title: `Edge B.R.O. Client Recovery Kiosk`
  - **Russian:**
    - App Name: `Edge B.R.O.`
    - Subtitle: `Оркестратор бэкапа и восстановления Edge`
    - Kiosk Title: `Edge B.R.O. Панель Восстановления Клиента`
  - **Ukrainian:**
    - App Name: `Edge B.R.O.`
    - Subtitle: `Оркестратор бекапу та відновлення Edge`
    - Kiosk Title: `Edge B.R.O. Панель Відновлення Клієнта`
- Remove the `systemOnline` translation key.

### HTML Title (index.html)
- Update `<title>` to `Edge B.R.O. — Edge Backup & Restore Orchestrator`.

---

## Verification Plan

### Automated Tests
- Build frontend (`npm run build` or similar) to verify TypeScript types and TSX compilation.
- Verify Jest/Vitest tests pass successfully (ensuring localization keys are correctly mapped and no tests fail due to missing keys).

### Manual Verification
- Deploy/run the app locally and open the browser.
- Verify the header looks perfect on desktop and narrow screens (mobile-responsive test).
- Verify clicking on the new language switcher successfully updates the active locale across the entire page instantly.
- Verify clicking outside the dropdown closes it.
- Verify the main dashboard displays the new hero welcome graphic when no edge nodes are registered.
