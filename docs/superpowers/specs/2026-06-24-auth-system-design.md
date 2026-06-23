# Design Spec: Authentication & Authorization System for Backup-Edge-Restore Orchestrator

This document specifies the architecture, data models, API access matrix, session mechanics, and user interface additions for the authentication and authorization system.

---

## 1. Goal

Introduce a secure, role-based authentication and authorization system for the Backup-Edge-Restore Orchestrator. The system must support two distinct classes of users:
1. **Administrators / Superadmin**: Access via a React-based web interface to manage nodes, settings, kiosks, backups, and schedules.
2. **Technician Kiosks (Edge Nodes)**: Access via automated background processes to pull node metadata, check task logs, and download backup repositories.

The separation between modes must prevent kiosks (or client ISO networks) from possessing or gaining administrative control over the orchestrator.

---

## 2. Core Architecture

### Database Schema (`models.User`)
A new table `users` will be added to the database:
- `id` (Integer, Primary Key)
- `username` (String, Unique, Index, Non-nullable)
- `hashed_password` (String, Non-nullable)
- `name` (String, Non-nullable)
- `phone` (String, Nullable)
- `telegram_id` (String, Nullable)
- `comment` (Text, Nullable) - Read/write restricted to the Superadmin
- `is_superadmin` (Boolean, Default: False)

### Superadmin Configuration & Seeding
1. **Startup Check**: During FastAPI's `startup_db_init` sequence, the backend checks if there is any user with `is_superadmin=True` in the database.
2. **Dynamic Seeding**: If no superadmin is found:
   - Reads `SUPERADMIN_USERNAME` (default: `admin`) and `ADMIN_PASSWORD` (default: `q1w2e3r4`) from the `.env` file.
   - Hashes the password using `bcrypt` (or `passlib.context.CryptContext`).
   - Seeds the superadmin into the database with `is_superadmin=True` and comment `"System-seeded superadmin"`.
3. **Immutability of `.env` after Setup**: Once a superadmin exists in the database, the startup check is skipped. Changes to `.env` passwords will not overwrite database passwords. This allows the superadmin to safely change their password via the UI.
4. **Recovery Path**: If the superadmin is deleted from the database, restarting the backend service will automatically re-seed it with the credentials specified in `.env`.

---

## 3. Session Security & Token Verification

### Dual Session Authentication
- **Administrators (Web Browser)**:
  - Exchanged via `POST /api/auth/login`.
  - The backend returns a JWT token but **also sets it** in an HTTP-only secure cookie named `admin_session`.
  - Cookie attributes: `HttpOnly`, `SameSite=Lax`, `Secure` (based on HTTPS deployment).
  - The React frontend uses this session automatically. This design mitigates the risk of XSS-based token theft.
- **Kiosks (API Clients)**:
  - Authenticate using the `Authorization: Bearer <kiosk_token>` HTTP header.
  - The `kiosk_token` is a secure hex token generated during the one-time secure pairing handshake.

### FastAPI Dependencies
- `get_current_auth`: Resolves authentication context.
  - Tries to read from the `Authorization` header.
  - If missing, checks the `admin_session` cookie.
  - Validates the token either as a JWT (Admin/Superadmin) or against active approved tokens in the `kiosks` table (Kiosk).
- `require_admin`: Ensures the authenticated subject is an administrator.
- `require_superadmin`: Ensures the authenticated subject is the superadmin.
- `require_kiosk_or_admin`: Restricts access to both authenticated admins and approved kiosks.

---

## 4. API Endpoint Access Control Matrix

| Endpoint Route | Allowed Roles | Description |
| :--- | :--- | :--- |
| `POST /api/auth/login` | Public | Authenticates credentials and issues token/cookie. |
| `POST /api/auth/logout` | Public | Clears the `admin_session` cookie. |
| `GET /api/version` | Public | Returns version details and mode check. |
| `POST /api/kiosks/handshake` | Public (Requires pre-paired key) | Pairs the kiosk and issues its API token. |
| `GET /api/nodes` | Admin, Kiosk | Retrieves node configurations. |
| `GET /api/nodes/{node_id}/history` | Admin, Kiosk | Retrieves node backup archives. |
| `GET /api/tasks` | Admin, Kiosk | Lists system tasks. |
| `GET /api/tasks/{task_id}` | Admin, Kiosk | Monitors details / streaming logs of a task. |
| `GET /api/iso/repos/{hostname}/download` | Admin, Kiosk | Streams the repository archives. |
| `PUT /api/users/profile` | Admin | Any admin can modify their name, phone, telegram, or password. |
| `GET /api/auth/me` | Admin | Returns current user profile (username, name, is_superadmin). |
| `GET /api/settings`, `POST /api/settings` | Admin | Manages global settings. |
| All other `/api/nodes/*`, `/api/groups/*`, `/api/iso/*`, `/api/kiosks/*` | Admin | Management and trigger endpoints. |
| `GET /api/users` | Superadmin | Lists all administrators (returns `comment` field). |
| `POST/PUT/DELETE /api/users` | Superadmin | Creates, updates, and deletes admin accounts. |

---

## 5. Frontend & UI Flow

### Login Screen
- A sleek, centered glassmorphic login interface matching the deep dark theme.
- Utilizes CSS transitions (`animate-modal-in` / `animate-fade-in`) on mount.
- Displays appropriate error messaging in three languages (EN, RU, UK).

### Admin Management Tab
- Rendered under the **Settings** section, visible **only** to the Superadmin (`is_superadmin == true` in profile payload).
- Form fields include: Username, Name, Phone Number, Telegram ID, Comment.
- The `Comment` column is hidden from standard admins and is only queryable/writeable by the superadmin.

### Profile Dropdown
- Current user badge in the header.
- Dropdown options: "Edit Profile" (modal for password and info updates) and "Logout".

---

## 6. Internationalization (i18n)

All UI additions will support English, Russian, and Ukrainian localizations. Text key lengths are designed to prevent layout shifts.

Key translations will include:
- `loginTitle`, `loginUsername`, `loginPassword`, `loginSubmit`, `loginError`
- `tabAdmins`, `createAdmin`, `editAdmin`, `deleteAdminConfirm`, `adminComment`
- `editProfile`, `logoutButton`
