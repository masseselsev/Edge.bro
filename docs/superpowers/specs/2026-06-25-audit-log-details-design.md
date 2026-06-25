# Design Spec: Detailed User Audit Diffs & Kiosk Actions Logging

Implement:
1. Property diff tracking for backend modifications (e.g. `Timezone: 'Europe/Moscow' ➔ 'Europe/London'`) and render these inside a premium glassmorphic hover tooltip in the frontend.
2. Logging for connected kiosks' actions (first-time handshakes, auto-handshakes, and repository downloads).
3. A separate settings sub-tab "Kiosk Logs" to display these logs.

## Requirements

1. **Detailed Settings Logging**:
   Compare the incoming settings payload against current database values inside `backend/routers/settings.py` to compile a comma-separated list of differences.
2. **Detailed User Profile Logging**:
   Compare updated user properties inside `backend/routers/users.py` (`name`, `phone` / `contact`, `telegram_id`, `comment`, `is_admin_plus`) and log changes.
3. **Detailed Kiosk Profile Logging**:
   Compare updated kiosk properties inside `backend/routers/kiosks.py` (`name`, `contact`, `comment`) and log changes.
4. **Detailed Node Actions Logging**:
   Compare updated node notes and group changes.
5. **Kiosk Connection & Download Logging**:
   - Log `/handshake` pairings as `"Kiosk Connected (Handshake)"`.
   - Log `/auto-handshake` checks.
   - Log `/repos/{hostname}/download` repository sync requests as `"Download Repository"`, displaying the file size.
   - Standardize logged username as `"Kiosk: <name> (UUID: <uuid>)"` or `"Kiosk: <uuid>"`.
6. **Query Filter by Type**:
   Expose query filter on `GET /api/users/audit-logs?type=admin|kiosk` to query admin actions or kiosk actions.
7. **Premium Hover Tooltip in UI**:
   Render absolute-positioned tooltip inside the Details cell in `AuditLogsTab.tsx` with transition animations and highlight the arrow `➔`.
8. **Kiosk Logs Tab**:
   Add `Kiosk Logs` settings sub-tab in settings rendering the same `AuditLogsTab` with `type="kiosk"`.

## Technical Specification

### 1. Backend Modifications

#### global settings update (`backend/routers/settings.py`)
Compare old vs new settings:
```python
changes = []
fields = [
    ("borg_ssh_port", "Borg SSH Port"),
    ("borg_repo_path", "Repository Path"),
    ("keep_daily", "Keep Daily"),
    ("keep_weekly", "Keep Weekly"),
    ("keep_monthly", "Keep Monthly"),
    ("global_exclusions", "Global Exclusions"),
    ("orchestrator_ip", "Orchestrator IP"),
    ("timezone", "Timezone"),
    ("language", "Language"),
    ("default_compression", "Compression"),
    ("default_cpu_quota", "CPU Quota"),
    ("server_ips", "Server IPs"),
]
for attr, label in fields:
    old_val = getattr(settings, attr)
    new_val = getattr(payload, attr)
    if old_val != new_val:
        changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")

old_policy = settings.retention_policy or {}
new_policy = payload.retention_policy.model_dump() if payload.retention_policy else {}
if old_policy != new_policy:
    policy_changes = []
    for pk in ["type", "keep_last", "within_value", "within_unit"]:
        op_val = old_policy.get(pk)
        np_val = new_policy.get(pk)
        if op_val != np_val:
            policy_changes.append(f"Retention {pk.replace('_', ' ')}: '{op_val}' ➔ '{np_val}'")
    if policy_changes:
        changes.extend(policy_changes)
```

#### User Update (`backend/routers/users.py`)
Compare user profile fields:
```python
changes = []
fields = [
    ("name", "Name"),
    ("phone", "Contact"),
    ("telegram_id", "Telegram ID"),
    ("comment", "Comment"),
    ("is_admin_plus", "Admin+ Privilege"),
]
for attr, label in fields:
    old_val = getattr(user, attr)
    new_val = getattr(payload, attr)
    if new_val is not None and old_val != new_val:
        changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")
if payload.password is not None:
    changes.append("Password updated")
```
Modify `get_audit_logs` endpoint to support type filtering:
```python
@router.get("/api/users/audit-logs", response_model=List[schemas.AuditLogResponse])
def get_audit_logs(
    type: Optional[str] = None,
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    query = db.query(models.AuditLog)
    if type == "kiosk":
        query = query.filter(models.AuditLog.username.like("Kiosk%"))
    elif type == "admin":
        query = query.filter(~models.AuditLog.username.like("Kiosk%"))
    return query.order_by(models.AuditLog.created_at.desc()).limit(1000).all()
```

#### Kiosk Update (`backend/routers/kiosks.py`)
Compare kiosk fields:
```python
changes = []
fields = [
    ("name", "Name"),
    ("contact", "Contact"),
    ("comment", "Comment"),
]
for attr, label in fields:
    old_val = getattr(kiosk, attr)
    new_val = getattr(req, attr)
    if new_val is not None and old_val != new_val:
        changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")
```
Add logging to handshake pairing:
```python
    kiosk_name = f"Kiosk: {kiosk.name or kiosk.uuid}"
    if kiosk.name and kiosk.uuid:
        kiosk_name = f"Kiosk: {kiosk.name} (UUID: {kiosk.uuid})"
    log_user_action(db, kiosk_name, "Kiosk Connected (Handshake)", "Kiosk paired and initialized SSH public key", request)
```
Standardize logging in auto-handshake and activation endpoints.

#### Kiosk Downloads (`backend/routers/iso.py`)
Add logging inside `download_repo`:
```python
    kiosk_name = "Kiosk"
    if isinstance(auth, models.Kiosk):
        kiosk_name = f"Kiosk: {auth.name} (UUID: {auth.uuid})" if auth.name else f"Kiosk: {auth.uuid}"
    elif isinstance(auth, models.User):
        kiosk_name = f"Admin: {auth.username}"
        
    def get_format_size(size_bytes):
        if size_bytes == 0: return "0 B"
        import math
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    formatted_size = get_format_size(total_size)
    log_user_action(db, kiosk_name, "Download Repository", f"Downloaded archive/repository for node '{hostname}' (Size: {formatted_size})", request)
```

---

### 2. Frontend Modifications

#### Reusable Tooltip & Tab Component (`AuditLogsTab.tsx`)
```tsx
interface AuditLogsTabProps {
  type?: 'admin' | 'kiosk';
  timezone?: string;
}

// Inside AuditLogsTab:
const fetchLogs = async () => {
  setLoading(true);
  setError('');
  try {
    const url = `/api/users/audit-logs${type ? `?type=${type}` : ''}`;
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      setLogs(data);
    } else { ... }
  } catch (e) { ... }
};
```
Change header title dynamically:
`{type === 'kiosk' ? (t('tabKioskLogs') || 'Kiosk Logs') : (t('tabAuditLogs') || 'Audit Logs')}`
Description dynamically:
`{type === 'kiosk' ? (t('kioskLogsSub') || 'Monitor connected kiosk handshakes and archive downloads.') : (t('auditLogsSub') || 'Monitor administrative actions and user login attempts.')}`

Tooltip renderer:
```tsx
const renderDetailsCell = (details: string | null) => {
  if (!details) return <span className="text-zinc-650">—</span>;

  const isDiff = details.includes('➔');
  let header = "";
  let items: string[] = [];
  
  if (isDiff) {
    const colonIndex = details.indexOf(':');
    if (colonIndex !== -1) {
      header = details.substring(0, colonIndex).trim();
      const changesStr = details.substring(colonIndex + 1).trim();
      items = changesStr.split(/,\s*(?![^()]*\))/);
    } else {
      items = [details];
    }
  } else {
    items = [details];
  }

  const formatItem = (item: string) => {
    if (item.includes('➔')) {
      const parts = item.split('➔');
      return (
        <>
          <span className="text-zinc-350">{parts[0]}</span>
          <span className="text-indigo-400 font-bold px-1 text-[10px]">➔</span>
          <span className="text-zinc-150">{parts[1]}</span>
        </>
      );
    }
    return <span className="text-zinc-300">{item}</span>;
  };

  return (
    <div className="relative group cursor-help py-1">
      <span className="truncate block max-w-xs sm:max-w-md text-zinc-300">{details}</span>
      
      {/* Premium Glassmorphic Tooltip */}
      <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 w-80 p-3 bg-zinc-950/95 backdrop-blur-md border border-zinc-800 rounded-xl shadow-2xl opacity-0 scale-95 pointer-events-none group-hover:opacity-100 group-hover:scale-100 transition-all duration-200 z-50 transform translate-y-1 group-hover:translate-y-0 text-left">
        {header && (
          <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-1.5 border-b border-zinc-850 pb-1">
            {header}
          </div>
        )}
        <ul className="space-y-1 text-[11px] leading-relaxed max-h-48 overflow-y-auto pr-1">
          {items.map((item, idx) => (
            <li key={idx} className="flex items-start gap-1">
              <span className="text-zinc-500 mt-0.5">•</span>
              <span className="break-all">{formatItem(item)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};
```

#### Settings Panel Tab Addition (`SettingsTab.tsx`)
1. Extend `activeSubTab` state:
   `const [activeSubTab, setActiveSubTab] = useState<'general' | 'admins' | 'audit' | 'kiosk_logs'>('general');`
2. Add Kiosk Logs sub-tab header:
   ```tsx
   <button
     type="button"
     onClick={() => setActiveSubTab('kiosk_logs')}
     className={`pb-2 border-b-2 px-1 transition-all cursor-pointer outline-none ${
       activeSubTab === 'kiosk_logs'
         ? 'border-indigo-500 text-zinc-150'
         : 'border-transparent text-zinc-450 hover:text-zinc-300'
     }`}
   >
     {t('tabKioskLogs') || 'Kiosk Logs'}
   </button>
   ```
3. Render sub-tab content:
   ```tsx
   ) : activeSubTab === 'kiosk_logs' && (currentUser?.is_superadmin || currentUser?.is_admin_plus) ? (
     <AuditLogsTab type="kiosk" timezone={timezone} />
   ```

#### i18n localization keys (`translations.ts`)
Add:
- `tabKioskLogs`: 'Kiosk Logs' / 'Логи киосков' / 'Логи кіосків'
- `kioskLogsSub`: 'Monitor connected kiosk handshakes and archive downloads.' / 'Мониторинг подключений киосков и скачиваний архивов.' / 'Моніторинг підключень кіосків та завантажень архівів.'

---

## Verification Plan

### 1. Automated Unit Tests
- Update unit tests in `test_audit_logs.py` to cover settings change logs and `type` queries (`?type=admin` vs `?type=kiosk`).
- Run `PYTHONPATH=. venv/bin/pytest tests/test_audit_logs.py` to verify backend logic.

### 2. Manual Verification
- Pair a kiosk and sync nodes list.
- Check "Settings -> Kiosk Logs". Verify handshake/auto-handshake and download entries are recorded under `Kiosk: Room A (UUID: <uuid>)`.
- Hover details and confirm formatting.
