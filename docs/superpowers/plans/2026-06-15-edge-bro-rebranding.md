# Edge B.R.O. Rebranding and Header Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand the application to "Edge B.R.O.", transition the header to a spacious two-row layout with an SVG shield logo, add a dynamic locale selector dropdown to the header, remove the old "System Online" indicator, and add the hero welcome logo image to the initial setup modal.

**Architecture:** 
1. Re-layout `App.tsx` global header into two rows (Row 1: Logo/Title + Buttons/Language Dropdown, Row 2: Tabs Navigation).
2. Create a dynamic, animated, click-outside-responsive Language Selector component inline in `App.tsx` or as a new component.
3. Update translations in `translations.ts` and document title in `index.html`.
4. Copy the generated logo asset to `frontend/public` and embed it in `App.tsx` empty-state modal.
5. Remove the duplicate language picker from `SettingsTab.tsx`.

**Tech Stack:** React, TypeScript, Tailwind CSS, Lucide Icons.

---

### Task 1: Asset Preparation & Title Update

**Files:**
- Create: `frontend/public/edge_bro_logo.png` (Copy from generated folder)
- Modify: `frontend/index.html`

- [ ] **Step 1: Copy the generated logo file to public folder**

Run:
```bash
cp /home/masse/projects/Backup-edge-Restore/.superpowers/brainstorm/203484-1781469442/content/edge_bro_logo.png /home/masse/projects/Backup-edge-Restore/frontend/public/edge_bro_logo.png
```

- [ ] **Step 2: Update HTML Document Title**

Modify `frontend/index.html` to change the `<title>` tag contents.

```html
<<<<
    <title>Borg Backup & Restore Orchestrator</title>
====
    <title>Edge B.R.O. — Edge Backup & Restore Orchestrator</title>
>>>>
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/index.html
git commit -m "chore: copy logo asset and update index.html title"
```


### Task 2: Update Translations & Remove "System Online" Keys

**Files:**
- Modify: `frontend/src/i18n/translations.ts`

- [ ] **Step 1: Update English, Russian, and Ukrainian translations**

Modify `frontend/src/i18n/translations.ts` to reflect the "Edge B.R.O." renaming and remove the `systemOnline` key.

```typescript
// For EN:
// In translations.ts:
<<<<
    systemOnline: 'System Online',
    configureOrchestratorIp: 'Configure Orchestrator IP',
    welcomeExplanation: 'No edge nodes registered yet. To ensure new nodes can communicate with this orchestrator, please verify and set the Orchestrator IP Address below.',
====
    configureOrchestratorIp: 'Configure Orchestrator IP',
    welcomeExplanation: 'No edge nodes registered yet. To ensure new nodes can communicate with this Edge B.R.O., please verify and set the Orchestrator IP Address below.',
>>>>

// For RU:
<<<<
    systemOnline: 'Система онлайн',
    configureOrchestratorIp: 'Настройка IP оркестратора',
    welcomeExplanation: 'Узлы ещё не добавлены. Подтвердите и укажите IP-адрес оркестратора ниже, чтобы новые узлы могли связаться с сервером.',
====
    configureOrchestratorIp: 'Настройка IP оркестратора',
    welcomeExplanation: 'Узлы ещё не добавлены. Подтвердите и укажите IP-адрес Edge B.R.O. ниже, чтобы новые узлы могли связаться с сервером.',
>>>>

// For UK:
<<<<
    systemOnline: 'Система онлайн',
    configureOrchestratorIp: 'Налаштування IP оркестратора',
    welcomeExplanation: 'Вузли ще не додані. Підтвердьте та вкажіть IP-адресу оркестратора нижче, щоб нові вузли могли зв\'язатися з сервером.',
====
    configureOrchestratorIp: 'Налаштування IP оркестратора',
    welcomeExplanation: 'Вузли ще не додані. Підтвердьте та вкажіть IP-адресу Edge B.R.O. нижче, щоб нові вузли могли зв\'язатися з сервером.',
>>>>
```

- [ ] **Step 2: Commit translations changes**

```bash
git add frontend/src/i18n/translations.ts
git commit -m "i18n: update application translations for Edge B.R.O. and remove systemOnline"
```


### Task 3: Refactor Settings tab (SettingsTab.tsx)

**Files:**
- Modify: `frontend/src/components/SettingsTab.tsx`

- [ ] **Step 1: Remove appLanguageLabel selection element**

Remove the duplicate Language field from the Settings page layout, since it will be available globally in the header.
In `frontend/src/components/SettingsTab.tsx`:

```tsx
<<<<
          {/* Language Selection */}
          <div className="bg-zinc-950 p-4 rounded-xl border border-zinc-800/80 space-y-4">
            <h4 className="text-xs font-bold text-white uppercase tracking-wider">Appearance</h4>
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('appLanguageLabel')}</label>
              <select
                value={language}
                onChange={(e) => setLanguageState(e.target.value as Language)}
                className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-white text-xs font-semibold focus:border-indigo-500 focus:outline-none cursor-pointer"
              >
                <option value="en">English (EN)</option>
                <option value="ru">Русский (RU)</option>
                <option value="uk">Українська (UK)</option>
              </select>
            </div>
          </div>
====
>>>>
```
*(Verify line range around 335-352 in `SettingsTab.tsx` before replacing).*

- [ ] **Step 2: Commit settings change**

```bash
git add frontend/src/components/SettingsTab.tsx
git commit -m "refactor: remove language selector from Settings tab"
```


### Task 4: Re-layout Global Header & Add Language Selector Dropdown (App.tsx)

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Implement two-row header with SVG logo, Edge B.R.O. name, and language selector dropdown**

We will:
1. Replace the Lucide `Server` icon with a custom animated inline SVG icon (Shield + Disk).
2. Restructure the header layout into a first row (Title, Version, Subtitle, Kiosk buttons, and Language Dropdown) and a second row (Tab navigation buttons).
3. Implement `LanguageDropdown` component right inside the header, utilizing `TranslationContext` hooks `language` and `setLanguage` and sending updates to backend settings API when language changes.
4. Add click-outside detection for the dropdown to close it when the user clicks elsewhere.
5. Embed `/edge_bro_logo.png` in `showIpPromptModal` modal box.

In `frontend/src/App.tsx`:
Replace the header block (lines 183 to 337):
```tsx
<<<<
      {/* Global Header */}
      <header className="bg-zinc-900/60 backdrop-blur-md border-b border-zinc-800/80 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 min-h-16 flex flex-col lg:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="p-2 bg-indigo-600 rounded-lg shadow-indigo-600/20 shadow-md">
              <Server className="text-white" size={20} />
            </div>
            <div>
              <h1 className="text-base font-bold text-white tracking-tight leading-none flex items-center gap-2">
                Borg Restore Orchestrator
                <span className="text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
              </h1>
              <p className="text-[10px] text-zinc-500 font-semibold mt-0.5 uppercase tracking-wider">Fleet Edge Bare-Metal Flasher</p>
            </div>
          </div>

          {/* Tab Navigation */}
          <nav className="flex flex-wrap items-center justify-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/60">
            ...
          </nav>

          <div className="flex flex-wrap items-center justify-center gap-3 flex-shrink-0">
            ...
            <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2.5 py-1 rounded-full font-bold uppercase tracking-wider animate-pulse-subtle">
              {t('systemOnline')}
            </span>
          </div>
        </div>
      </header>
====
      {/* Global Header */}
      <header className="bg-zinc-900/80 backdrop-blur-md border-b border-zinc-800/80 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 space-y-3">
          {/* Row 1: Logo/Title + Quick Actions / Language Selector */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            {/* Left: Brand Identity with SVG logo */}
            <div className="flex items-center gap-3 flex-shrink-0">
              <div className="relative p-2 bg-indigo-600/15 border border-indigo-500/30 rounded-lg shadow-lg flex items-center justify-center w-9 h-9">
                <svg className="w-5 h-5 text-indigo-400 filter drop-shadow-[0_0_4px_rgba(99,102,241,0.6)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-orange-500 rounded-full animate-ping"></span>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-orange-500 rounded-full"></span>
              </div>
              <div>
                <h1 className="text-base font-bold text-white tracking-tight leading-none flex items-center gap-2">
                  <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded font-mono font-bold text-xs uppercase tracking-wider">Edge B.R.O.</span>
                  <span className="text-[10px] bg-zinc-800 text-zinc-400 border border-zinc-700 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
                </h1>
                <p className="text-[9px] text-zinc-500 font-semibold mt-1 uppercase tracking-wider">
                  {language === 'ru' ? 'Оркестратор бэкапа и восстановления Edge' : language === 'uk' ? 'Оркестратор бекапу та відновлення Edge' : 'Edge Backup & Restore Orchestrator'}
                </p>
              </div>
            </div>

            {/* Right: Actions + Custom Language Switcher Dropdown */}
            <div className="flex flex-wrap items-center justify-center gap-3 flex-shrink-0">
              {isKiosk && (
                <>
                  <button
                    onClick={handleToggleMode}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
                    title="Toggle restoration mode"
                  >
                    {restoreMode === 'online' ? (
                      <>
                        <Globe2 size={13} className="text-indigo-400" />
                        <span>{t('modeOnline')}</span>
                      </>
                    ) : (
                      <>
                        <HardDrive size={13} className="text-amber-400" />
                        <span>{t('modeOffline')}</span>
                      </>
                    )}
                  </button>
                  <button
                    onClick={() => setShowNetworkModal(true)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
                  >
                    {networkStatus?.wired?.connected ? (
                      <>
                        <Globe2 size={13} className="text-emerald-400" />
                        <span>{t('wiredLink')}</span>
                      </>
                    ) : networkStatus?.wifi?.connected ? (
                      <>
                        <Wifi size={13} className="text-emerald-400" />
                        <span>{networkStatus.wifi.ssid}</span>
                      </>
                    ) : (
                      <>
                        <Globe2 size={13} className="text-rose-400" />
                        <span className="text-rose-400 font-bold">{t('offline')}</span>
                      </>
                    )}
                  </button>
                  <button
                    onClick={handleExitKiosk}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-950/20 hover:bg-red-950/40 border border-red-900/30 hover:border-red-900/60 text-xs text-red-400 font-bold transition-all duration-200 cursor-pointer"
                    title="Exit Kiosk Mode"
                  >
                    <LogOut size={13} />
                    <span>{t('exitKiosk')}</span>
                  </button>
                </>
              )}

              {/* Language Dropdown Selector */}
              <LanguageSelector />
            </div>
          </div>

          {/* Row 2: Tab Navigation Buttons */}
          <div className="border-t border-zinc-800/60 pt-2 flex justify-start">
            <nav className="flex flex-wrap items-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/60">
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('fleet')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'fleet'
                      ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Server size={14} /> {t('tabFleet')}
                </button>
              )}
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('schedule')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'schedule'
                      ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Calendar size={14} /> {t('tabSchedule')}
                </button>
              )}
              <button
                onClick={() => setActiveTab('flasher')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'flasher'
                    ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <HardDrive size={14} /> {t('tabFlasher')}
              </button>
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('clientiso')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'clientiso'
                      ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Cpu size={14} /> {t('liveUsbGenerator')}
                </button>
              )}
              <button
                onClick={() => setActiveTab('history')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'history'
                    ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <History size={14} /> {t('tabHistory')}
              </button>
              <button
                onClick={() => setActiveTab('logs')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'logs'
                    ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <Terminal size={14} /> {t('tabLogs')}
              </button>
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('settings')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'settings'
                      ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Gear size={14} /> {t('tabSettings')}
                </button>
              )}
            </nav>
          </div>
        </div>
      </header>
>>>>
```

Also, modify `showIpPromptModal` to render `/edge_bro_logo.png` above the welcome text:
```tsx
<<<<
            <p className="text-xs text-zinc-300 leading-relaxed font-medium">
              {t('welcomeExplanation')}
            </p>
====
            <div className="flex justify-center py-2 bg-zinc-950/60 rounded-xl border border-zinc-800/80">
              <img src="/edge_bro_logo.png" alt="Edge B.R.O. Logo" className="w-40 h-40 object-contain rounded-lg shadow-lg border border-indigo-500/20" />
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-medium">
              {t('welcomeExplanation')}
            </p>
>>>>
```

- [ ] **Step 2: Add `LanguageSelector` component and imports**

Let's define the `LanguageSelector` component inside `App.tsx` (above `AppContent`). It needs to import/use standard React hooks, useTranslation, handle dropdown state, and handle click-outside closing.

Add imports if needed at the top of `App.tsx`:
```typescript
import { useRef } from 'react';
import type { Language } from './i18n/translations';
```

Add the `LanguageSelector` component definition inside `App.tsx`:
```tsx
function LanguageSelector() {
  const { language, setLanguage } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = async (lang: Language) => {
    setLanguage(lang);
    setIsOpen(false);
    
    // Save selected language to settings database
    try {
      const getRes = await fetch('/api/settings');
      if (getRes.ok) {
        const settings = await getRes.ok ? await getRes.json() : {};
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...settings,
            language: lang
          })
        });
      }
    } catch (err) {
      console.error('Failed to sync language selection to settings backend:', err);
    }
  };

  const labels: Record<Language, string> = {
    en: 'English (EN)',
    ru: 'Русский (RU)',
    uk: 'Українська (UK)'
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
      >
        <Globe2 size={13} className="text-zinc-400" />
        <span>{labels[language] || language.toUpperCase()}</span>
        <svg className={`w-3 h-3 text-zinc-500 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1.5 w-40 rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
          {(['en', 'ru', 'uk'] as Language[]).map((lang) => (
            <button
              key={lang}
              onClick={() => handleSelect(lang)}
              className={`w-full text-left px-3 py-2 text-xs font-semibold rounded-md transition-colors flex items-center justify-between ${
                language === lang
                  ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/20'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
              }`}
            >
              <span>{labels[lang]}</span>
              {language === lang && <span className="text-[10px] text-indigo-400">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit refactored App.tsx**

```bash
git add frontend/src/App.tsx
git commit -m "feat: refactor App.tsx header layout and integrate dynamic language selector dropdown"
```


### Task 5: Build Verification & Run Tests

**Files:**
- Test: Build validation

- [ ] **Step 1: Build the frontend bundle**

Verify there are no TypeScript or compilation errors.
Run:
```bash
npm run build --prefix frontend
```
Expected: Success with no errors.

- [ ] **Step 2: Run backend tests**

Verify existing backend tests still pass cleanly.
Run:
```bash
pytest backend/tests -v
```
Expected: PASS.
