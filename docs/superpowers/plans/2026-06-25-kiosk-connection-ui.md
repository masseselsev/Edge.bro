# Kiosk Connection UI Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent kiosk setup deadlocks by displaying the standard page header/footer unconditionally and rendering a non-blocking bottom connection card.

**Architecture:** Render the main dashboard page unconditionally on kiosk startup. Relocate the `BlockedKioskScreen` component into a bottom connection card rendered below the active tab content. Pass the `kioskStatus` to the `FlasherTab` component and disable/overlay its main interactive components when the kiosk is not approved.

**Tech Stack:** React, TypeScript, Tailwind CSS.

## Global Constraints

- **UI Styling & Animations**: All dropdown lists and modal windows MUST use CSS transition animations.
- **Multi-Language Support (i18n)**: All new features and UI text additions must support internationalization.

---

### Task 1: Refactor App Shell Layout and Routing

**Files:**
- Modify: [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx:892-902)
- Modify: [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx:1183-1190)

- [ ] **Step 1: Remove full-screen blocking render condition**

In [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), remove the root layout blocking condition.

Replace lines 892-902:
```typescript
  if (appReady && isKiosk && kioskStatus !== 'APPROVED') {
    return (
      <BlockedKioskScreen 
        status={kioskStatus} 
        onActivationRequested={() => setKioskStatus('PENDING')}
        onPairingSuccess={() => setKioskStatus('APPROVED')}
        appVersion={appVersion}
        kioskUuid={kioskUuid}
      />
    );
  }
```
with:
```typescript
  // Render main app layout unconditionally once appReady is true.
```

- [ ] **Step 2: Pass kioskStatus to FlasherTab component**

In [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx) inside `renderTabContent`, pass `kioskStatus` to `FlasherTab`.

Replace line 871:
```typescript
        return <FlasherTab onViewLogs={handleViewLogs} timezone={tz} restoreMode={restoreMode} isKiosk={isKiosk} />;
```
with:
```typescript
        return <FlasherTab onViewLogs={handleViewLogs} timezone={tz} restoreMode={restoreMode} isKiosk={isKiosk} kioskStatus={kioskStatus} />;
```

- [ ] **Step 3: Render the connection card at the bottom of the page**

In [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), render `BlockedKioskScreen` as a bottom status card inside `<main>` when in online mode and not approved.

Replace lines 1183-1188:
```typescript
      {/* Main Body */}
      <main className={`flex-1 max-w-7xl w-full mx-auto px-6 py-8 ${isKiosk ? 'pb-20' : ''}`}>
        <div key={activeTab} className="animate-fade-in">
          {renderTabContent()}
        </div>
      </main>
```
with:
```typescript
      {/* Main Body */}
      <main className={`flex-1 max-w-7xl w-full mx-auto px-6 py-8 ${isKiosk ? 'pb-20' : ''}`}>
        <div key={activeTab} className="animate-fade-in">
          {renderTabContent()}
        </div>
        
        {isKiosk && restoreMode === 'online' && kioskStatus !== 'APPROVED' && (
          <div className="mt-8 border-t border-zinc-800/80 pt-8 animate-fade-in">
            <BlockedKioskScreen 
              status={kioskStatus} 
              onActivationRequested={() => setKioskStatus('PENDING')}
              onPairingSuccess={() => setKioskStatus('APPROVED')}
              appVersion={appVersion}
              kioskUuid={kioskUuid}
            />
          </div>
        )}
      </main>
```

- [ ] **Step 4: Commit changes**

```bash
git add frontend/src/App.tsx
git commit -m "feat: render app shell unconditionally and place connection card at page bottom"
```

---

### Task 2: Refactor BlockedKioskScreen to a Card UI

**Files:**
- Modify: [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx:127-350)

- [ ] **Step 1: Update BlockedKioskScreen signature and return block**

In [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), change `BlockedKioskScreen` layout from full-screen layout to a bottom card layout. Remove the top bar and outer wrapper page elements.

Replace lines 199-213:
```typescript
  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-between bg-zinc-950 text-zinc-100 font-sans select-none">
      {/* Top Bar with Language Selector */}
      <div className="flex justify-between items-center px-6 py-4 border-b border-zinc-900 bg-zinc-900/35 backdrop-blur-md">
        <div className="flex items-center gap-2">
          <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded font-mono font-bold text-xs uppercase tracking-wider">Edge B.R.O.</span>
          <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
        </div>
        <LanguageSelector />
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md w-full p-8 bg-zinc-900/50 border border-zinc-800/80 rounded-3xl shadow-2xl space-y-6 text-center animate-fade-in relative overflow-hidden">
          <div className="absolute inset-0 bg-gradient-to-b from-indigo-500/5 via-transparent to-transparent pointer-events-none" />
```
with:
```typescript
  return (
    <div className="w-full flex items-center justify-center p-4">
      <div className="max-w-md w-full p-8 bg-zinc-900/50 border border-zinc-800/80 rounded-3xl shadow-2xl space-y-6 text-center animate-fade-in relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-indigo-500/5 via-transparent to-transparent pointer-events-none" />
```

- [ ] **Step 2: Update footer block in BlockedKioskScreen**

Remove the bottom-fixed footer from the component render block.

Replace lines 340-350:
```typescript
      </div>

      {/* Footer Info */}
      <div className="text-center pb-8 space-y-1">
        <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">
          {t('kioskBlockedThisId')}
        </span>
        <span className="font-mono text-xs font-black text-indigo-400 bg-indigo-500/5 border border-indigo-500/10 px-3 py-1 rounded-lg">
          {kioskUuid || 'UNKNOWN'}
        </span>
      </div>
    </div>
  );
```
with:
```typescript
        {/* Footer Info inside card */}
        <div className="text-center pt-4 border-t border-zinc-800/50 space-y-1">
          <span className="text-[9px] text-zinc-400 font-bold uppercase tracking-wider block">
            {t('kioskBlockedThisId')}
          </span>
          <span className="font-mono text-xs font-black text-indigo-400 bg-indigo-500/5 border border-indigo-500/10 px-3 py-1 rounded-lg inline-block">
            {kioskUuid || 'UNKNOWN'}
          </span>
        </div>
      </div>
    </div>
  );
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/src/App.tsx
git commit -m "feat: refactor connection screen to bottom card layout style"
```

---

### Task 3: Flasher Tab Guard Integration

**Files:**
- Modify: [FlasherTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FlasherTab.tsx)

- [ ] **Step 1: Add kioskStatus property to FlasherTabProps**

In [FlasherTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FlasherTab.tsx), declare `kioskStatus` in the interface and function properties.

Replace line 39-44:
```typescript
  restoreMode?: 'offline' | 'online';
  isKiosk?: boolean;
}

export default function FlasherTab({ onViewLogs, timezone, restoreMode = 'offline', isKiosk = false }: FlasherTabProps) {
```
with:
```typescript
  restoreMode?: 'offline' | 'online';
  isKiosk?: boolean;
  kioskStatus?: string;
}

export default function FlasherTab({ onViewLogs, timezone, restoreMode = 'offline', isKiosk = false, kioskStatus = 'APPROVED' }: FlasherTabProps) {
```

- [ ] **Step 2: Add non-approved warning overlay or banner**

In [FlasherTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FlasherTab.tsx), render a warning overlay over the main interactive section when in online mode and not approved.

Replace line 241-242:
```typescript
  return (
    <div className="space-y-6">
```
with:
```typescript
  const isOnlineWaitingApproval = isKiosk && restoreMode === 'online' && kioskStatus !== 'APPROVED';

  return (
    <div className="space-y-6 relative">
      {isOnlineWaitingApproval && (
        <div className="absolute inset-0 z-30 bg-zinc-950/70 backdrop-blur-sm rounded-3xl flex flex-col items-center justify-center p-6 text-center animate-fade-in border border-zinc-800/50">
          <div className="max-w-md space-y-4">
            <div className="inline-flex p-4 bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 rounded-2xl">
              <Loader2 size={32} className="animate-spin" />
            </div>
            <h3 className="text-lg font-bold text-zinc-150">Waiting for Server Approval</h3>
            <p className="text-xs text-zinc-400 leading-relaxed font-medium">
              This kiosk is waiting to connect to the server. You can configure the network using the indicator in the header, or toggle to Offline Mode to restore from local USB storage.
            </p>
          </div>
        </div>
      )}
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/src/components/FlasherTab.tsx
git commit -m "feat: add server connection guard overlay to FlasherTab"
```

---

### Task 4: UI/UX Build Verification

- [ ] **Step 1: Build the frontend bundle**

Verify there are no compile or TypeScript syntax errors.

Run: `npm --prefix frontend run build`
Expected: SUCCESS

- [ ] **Step 2: Commit all remaining changes**

```bash
git status
```
Expected: Clean working tree.
