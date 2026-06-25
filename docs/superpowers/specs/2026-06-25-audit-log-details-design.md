# Design Spec: Detailed User Action Audit Logs (Before/After Diffs)

Implement a detailed audit log logging mechanism on the backend to track exact property modifications (e.g. `Timezone: 'Europe/Moscow' ➔ 'Europe/London'`), and render these changes cleanly inside a premium glassmorphic tooltip in the frontend settings panel.

## Requirements

1. **Detailed Settings Logging**:
   Compare the incoming settings payload against current database values inside `backend/routers/settings.py` to compile a comma-separated list of differences.
2. **Detailed User Profile Logging**:
   Compare updated user properties inside `backend/routers/users.py` (`name`, `phone` / `contact`, `telegram_id`, `comment`, `is_admin_plus`) and log changes.
3. **Detailed Kiosk Profile Logging**:
   Compare updated kiosk properties inside `backend/routers/kiosks.py` (`name`, `contact`, `comment`) and log changes.
4. **Detailed Node Actions Logging**:
   Compare updated node notes and group changes.
5. **Premium Floating Tooltip**:
   Replace native browser tooltips in the `Details` column of `AuditLogsTab.tsx` with a styled, absolute-positioned glassmorphic overlay containing list items and highlight the transition arrow `➔`.

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
    # Extract only changed sub-policy elements for brief log
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

#### Node Notes & Groups (`backend/routers/nodes.py`)
- In `update_node_notes`: record `Notes: '{old_notes}' ➔ '{new_notes}'`.
- In `assign_node_group`: check node's current `group_id` vs new `group_id` and query group names to log: `Group: '{old_group_name}' ➔ '{new_group_name}'`.

---

### 2. Frontend Modifications (`AuditLogsTab.tsx`)

Render a custom tooltip wrapper in the table cell:
```tsx
const renderDetailsCell = (details: string | null) => {
  if (!details) return <span className="text-zinc-650">—</span>;

  // Check if details is a diff-style log (starts with "Updated..." and contains "➔")
  const isDiff = details.includes('➔');
  
  // Parse elements
  let header = "";
  let items: string[] = [];
  
  if (isDiff) {
    const colonIndex = details.indexOf(':');
    if (colonIndex !== -1) {
      header = details.substring(0, colonIndex).trim();
      const changesStr = details.substring(colonIndex + 1).trim();
      // Split by comma followed by space
      items = changesStr.split(/,\s*(?![^()]*\))/); // avoid splitting commas inside parenthesis if any
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

---

## Verification Plan

### 1. Automated Unit Tests
- Add a new test in `test_audit_logs.py` simulating setting updates and checking that the difference details string is populated correctly with the `➔` token.
- Run `PYTHONPATH=. venv/bin/pytest tests/test_audit_logs.py` to verify backend correctness.

### 2. Manual Verification
- Log in, edit settings timezone, edit user profile (e.g. comment), and assign a node group.
- Go to Settings -> Audit Logs.
- Hover over the updated details cells and verify that the custom styled tooltip shows up with the old/new values styled and transition arrows highlighted.
