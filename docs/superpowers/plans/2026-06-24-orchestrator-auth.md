# Orchestrator Authentication & Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a secure, role-based authentication and authorization system for the Backup-edge-Restore orchestrator that separates administrator actions from technician kiosk actions.

**Architecture:** Database-backed admin table, dynamic superadmin seeding, secure JWT authentication via HTTP-only cookies for admins, and token-based header authentication for paired kiosks.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, React (TypeScript), Tailwind CSS, PyJWT, Passlib (with bcrypt).

## Global Constraints

- **Strict Python Type Hinting**: Always use Pydantic models for request/response serialization.
- **Maximum File Size**: No single file must exceed 500 lines. Split routers, tasks, and components when they grow.
- **Database Migrations**: Always use Alembic migrations for DB changes. Do not modify database schemas directly.
- **Secrets Management**: Read Borg Passphrase (`BORG_PASSPHRASE`) and Database credentials exclusively from environment variables/`.env`. Never store them in DB or VCS.
- **UI Styling & Animations**: All dropdown lists and modal windows MUST use CSS transition animations (e.g., `animate-modal-in`, `animate-dropdown-in`, `animate-fade-in`) to maintain the project's premium dynamic aesthetic. Fallback behavior must be ensured so that interfaces remain fully visible and usable in restricted environments (like the XFCE Live-CD) if hardware acceleration or animations are unsupported.
- **Multi-Language Support (i18n)**: All new features and UI text additions must support internationalization (English, Russian, Ukrainian). Keep text element lengths similar across languages to ensure the layout remains stable.

---

### Task 1: Add Backend Dependencies & Database Schema Migration

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/models.py`
- Modify: `backend/schemas.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: None
- Produces: `User` model, Alembic migration, Pydantic schemas, and superadmin seeding on startup.

- [ ] **Step 1: Write seeding tests**
  Create `backend/tests/test_auth.py` with testing database setup and a test for superadmin seeding.
  ```python
  import os
  import pytest
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker
  from database import Base
  import models
  from main import upgrade_settings # We will import our seeding logic here or test main.py directly

  TEST_DATABASE_URL = "sqlite:///./test_auth_db.db"

  @pytest.fixture
  def db_session():
      engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
      TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
      Base.metadata.create_all(bind=engine)
      db = TestingSessionLocal()
      try:
          yield db
      finally:
          db.close()
          Base.metadata.drop_all(bind=engine)
          if os.path.exists("./test_auth_db.db"):
              os.remove("./test_auth_db.db")
  ```

- [ ] **Step 2: Add Python dependencies**
  Add dependencies `pyjwt>=2.8.0` and `passlib[bcrypt]>=1.7.4` to `backend/requirements.txt`.
  Run: `pip install -r backend/requirements.txt` (or verify installation in Docker).

- [ ] **Step 3: Define User DB Model**
  Modify `/home/masse/projects/Backup-edge-Restore/backend/models.py` to add the `User` class.
  ```python
  class User(Base):
      __tablename__ = 'users'
      id = Column(Integer, primary_key=True, index=True)
      username = Column(String, unique=True, index=True, nullable=False)
      hashed_password = Column(String, nullable=False)
      name = Column(String, nullable=False)
      phone = Column(String, nullable=True)
      telegram_id = Column(String, nullable=True)
      comment = Column(Text, nullable=True)
      is_superadmin = Column(Boolean, default=False, nullable=False)
  ```

- [ ] **Step 4: Define user schemas**
  Modify `/home/masse/projects/Backup-edge-Restore/backend/schemas.py` to add Pydantic schemas.
  ```python
  from pydantic import BaseModel, Field
  from typing import Optional

  class UserBase(BaseModel):
      username: str = Field(..., min_length=3, max_length=50)
      name: str = Field(..., min_length=1, max_length=100)
      phone: Optional[str] = None
      telegram_id: Optional[str] = None

  class UserCreate(UserBase):
      password: str = Field(..., min_length=6)
      comment: Optional[str] = None

  class UserUpdate(BaseModel):
      name: Optional[str] = None
      phone: Optional[str] = None
      telegram_id: Optional[str] = None
      password: Optional[str] = None
      comment: Optional[str] = None

  class UserSelfUpdate(BaseModel):
      name: Optional[str] = None
      phone: Optional[str] = None
      telegram_id: Optional[str] = None
      password: Optional[str] = None

  class UserResponse(UserBase):
      id: int
      is_superadmin: bool
      comment: Optional[str] = None

      class Config:
          from_attributes = True

  class LoginPayload(BaseModel):
      username: str
      password: str
  ```

- [ ] **Step 5: Generate & execute migration**
  Generate an Alembic migration file for the `users` table.
  Run: `docker compose exec backend alembic revision --autogenerate -m "create users table"`
  Verify the generated migration file and run: `docker compose exec backend alembic upgrade head`

- [ ] **Step 6: Implement superadmin seeding**
  Implement superadmin seeding in `backend/main.py` under the `startup_db_init` event.
  ```python
  # In startup_db_init:
  def seed_superadmin(db: Session):
      from passlib.context import CryptContext
      pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
      superadmin = db.query(models.User).filter(models.User.is_superadmin == True).first()
      if not superadmin:
          import os
          username = os.getenv("SUPERADMIN_USERNAME", "admin")
          password = os.getenv("ADMIN_PASSWORD", "q1w2e3r4")
          hashed = pwd_context.hash(password)
          db_user = models.User(
              username=username,
              hashed_password=hashed,
              name="Super Administrator",
              is_superadmin=True,
              comment="System-seeded superadmin"
          )
          db.add(db_user)
          db.commit()
          print(f"Superadmin user '{username}' seeded successfully.")
  ```

- [ ] **Step 7: Run seeding tests**
  Run: `pytest backend/tests/test_auth.py`
  Expected: PASS

- [ ] **Step 8: Commit**
  Run: `git add backend/requirements.txt backend/models.py backend/schemas.py backend/main.py backend/alembic/versions/*`
  Commit message: `feat(auth): create users table and implement superadmin startup seeding`

---

### Task 2: Implement User Authentication and Guards (FastAPI Backend)

**Files:**
- Create: `backend/routers/users.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `models.User`, `schemas.LoginPayload`
- Produces: Router `users` endpoints, dependency guards `get_current_auth`, `require_admin`, `require_superadmin`, `require_kiosk_or_admin`.

- [ ] **Step 1: Write auth and guard tests**
  Add unit tests to `backend/tests/test_auth.py` for login token generation, password validation, JWT validation, and role check functionality.

- [ ] **Step 2: Create users router and token logic**
  Create `backend/routers/users.py`. Keep under 500 lines. Define cryptographic keys, functions, and router endpoints:
  ```python
  import os
  from datetime import datetime, timedelta
  from typing import Union
  from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
  from jose import JWTError, jwt
  from passlib.context import CryptContext
  from sqlalchemy.orm import Session
  from database import get_db
  import models
  import schemas

  JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-change-me")
  ALGORITHM = "HS256"
  ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 hours

  pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
  router = APIRouter()

  def get_password_hash(password: str) -> str:
      return pwd_context.hash(password)

  def verify_password(plain_password: str, hashed_password: str) -> bool:
      return pwd_context.verify(plain_password, hashed_password)

  def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
      to_encode = data.copy()
      expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
      to_encode.update({"exp": expire})
      return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
  ```

- [ ] **Step 3: Define dependency guards**
  ```python
  def get_current_auth(request: Request, db: Session = Depends(get_db)):
      token = None
      auth_header = request.headers.get("Authorization")
      if auth_header and auth_header.startswith("Bearer "):
          token = auth_header.split(" ")[1]
      else:
          token = request.cookies.get("admin_session")

      if not token:
          raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

      try:
          payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
          username: str = payload.get("sub")
          if username is None:
              raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
          user = db.query(models.User).filter(models.User.username == username).first()
          if not user:
              raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
          return user
      except JWTError:
          # Fallback to kiosk auth
          kiosk = db.query(models.Kiosk).filter(models.Kiosk.auth_token == token, models.Kiosk.status == "APPROVED").first()
          if kiosk:
              return kiosk
          raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session or token")

  def require_admin(auth = Depends(get_current_auth)) -> models.User:
      if not isinstance(auth, models.User):
          raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin permissions required")
      return auth

  def require_superadmin(auth = Depends(get_current_auth)) -> models.User:
      if not isinstance(auth, models.User) or not auth.is_superadmin:
          raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin permissions required")
      return auth

  def require_kiosk_or_admin(auth = Depends(get_current_auth)):
      return auth
  ```

- [ ] **Step 4: Implement Login, Logout, Profile APIs**
  Add endpoints to `/home/masse/projects/Backup-edge-Restore/backend/routers/users.py`:
  * `POST /api/auth/login`: verifies credentials, generates token, sets cookie `admin_session`, returns JSON payload.
  * `POST /api/auth/logout`: deletes cookie `admin_session`.
  * `GET /api/auth/me`: returns currently logged-in user profile.
  * `PUT /api/users/profile`: allows users to modify their own profile data and password.

- [ ] **Step 5: Include Users Router**
  Include the router in `backend/main.py`.
  ```python
  from routers import users as users_router
  app.include_router(users_router.router)
  ```

- [ ] **Step 6: Run tests and verify**
  Run: `pytest backend/tests/test_auth.py`
  Expected: PASS

- [ ] **Step 7: Commit**
  Run: `git add backend/routers/users.py backend/main.py backend/tests/test_auth.py`
  Commit message: `feat(auth): implement admin login, logout, profile APIs, and auth guards`

---

### Task 3: Secure Existing Routers with Auth Guards

**Files:**
- Modify: `backend/routers/settings.py`
- Modify: `backend/routers/nodes.py`
- Modify: `backend/routers/tasks.py`
- Modify: `backend/routers/restore.py`
- Modify: `backend/routers/stats.py`
- Modify: `backend/routers/iso.py`
- Modify: `backend/routers/groups.py`
- Modify: `backend/routers/kiosks.py`
- Modify: `backend/routers/network.py`
- Modify: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `require_admin`, `require_kiosk_or_admin` dependencies
- Produces: Protected HTTP endpoints on the orchestrator.

- [ ] **Step 1: Write integration tests for endpoint protection**
  Add tests inside `backend/tests/test_auth.py` verifying that:
  - Unauthorized calls to `GET /api/nodes` return `401`.
  - Kiosk token calls to `GET /api/nodes` return `200` (success).
  - Kiosk token calls to `POST /api/settings` return `403`.
  - Admin cookie/JWT calls to any router endpoint succeed.

- [ ] **Step 2: Inject guards into Routers**
  Modify routers and routes to require authentication:
  - **`routers/settings.py`**:
    - `get_version`: Public (keep as is).
    - `get_settings`, `update_settings`: require `Depends(require_admin)`.
  - **`routers/nodes.py`**:
    - `get_nodes`: requires `Depends(require_kiosk_or_admin)`.
    - All other routes (create, delete, prepare, backup): require `Depends(require_admin)`.
  - **`routers/tasks.py`**:
    - `get_tasks`, `get_task`: require `Depends(require_kiosk_or_admin)`.
  - **`routers/restore.py`**:
    - `get_history`: requires `Depends(require_kiosk_or_admin)`.
    - `trigger_restore`: requires `Depends(require_admin)`.
  - **`routers/stats.py`**:
    - `get_stats`: requires `Depends(require_admin)`.
  - **`routers/iso.py`**:
    - `download_repo`: Keep token parameter logic, but add fallback verification of header token via `require_kiosk_or_admin`.
    - All other routes (generate, status, download): require `Depends(require_admin)`.
  - **`routers/groups.py`**:
    - All routes: require `Depends(require_admin)`.
  - **`routers/kiosks.py`**:
    - `handshake`: Public (keep as is).
    - All other routes (create, delete, list, revoke): require `Depends(require_admin)`.
  - **`routers/network.py`**:
    - All routes: require `Depends(require_admin)`.

- [ ] **Step 3: Run all backend tests**
  Run: `pytest backend/tests/`
  Expected: PASS

- [ ] **Step 4: Commit**
  Run: `git add backend/routers/*.py backend/tests/test_auth.py`
  Commit message: `security(auth): apply auth dependencies to settings, nodes, tasks, restore, stats, and ISO routers`

---

### Task 4: Implement Administrator CRUD Management (Superadmin Only)

**Files:**
- Modify: `backend/routers/users.py`
- Modify: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `require_superadmin` dependency
- Produces: API routes to create, update, delete, and list additional admin accounts.

- [ ] **Step 1: Write CRUD tests**
  Add unit tests in `backend/tests/test_auth.py` verifying that:
  - Superadmin can create additional admins.
  - Standard admins cannot create admins (return `403`).
  - Comment fields are only returned to the superadmin.
  - Deleting/updating accounts works correctly.

- [ ] **Step 2: Implement User CRUD Endpoints**
  In `/home/masse/projects/Backup-edge-Restore/backend/routers/users.py`, implement:
  * `GET /api/users`: Requires `require_superadmin`. Returns `List[schemas.UserResponse]`.
  * `POST /api/users`: Requires `require_superadmin`. Creates a new admin from `schemas.UserCreate`. Hashed password using `get_password_hash`.
  * `PUT /api/users/{user_id}`: Requires `require_superadmin`. Updates selected admin from `schemas.UserUpdate`.
  * `DELETE /api/users/{user_id}`: Requires `require_superadmin`. Deletes user. Cannot delete self or last superadmin.

- [ ] **Step 3: Run all tests**
  Run: `pytest backend/tests/`
  Expected: PASS

- [ ] **Step 4: Commit**
  Run: `git add backend/routers/users.py backend/tests/test_auth.py`
  Commit message: `feat(auth): implement administrator user management CRUD APIs for superadmin`

---

### Task 5: React Frontend Integration (Login Page & User Admin UI)

**Files:**
- Create: `frontend/src/components/Login.tsx`
- Create: `frontend/src/components/AdminsTab.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/SettingsTab.tsx`
- Modify: `frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: Backend authentication and user management APIs.
- Produces: Interactive login form, dropdown menu in header, user profile modal, and user management tab.

- [ ] **Step 1: Add Translations**
  Modify `frontend/src/i18n/translations.ts` to add multi-language support (English, Russian, Ukrainian) for authentication fields.
  ```typescript
  export const translations = {
    en: {
      loginTitle: "Sign in to Edge B.R.O.",
      loginUsername: "Username",
      loginPassword: "Password",
      loginSubmit: "Sign In",
      loginError: "Invalid username or password",
      logoutButton: "Sign Out",
      // user crud translation keys...
    },
    ru: {
      loginTitle: "Вход в Edge B.R.O.",
      loginUsername: "Имя пользователя",
      loginPassword: "Пароль",
      loginSubmit: "Войти",
      loginError: "Неверное имя пользователя или пароль",
      logoutButton: "Выйти",
      // ...
    },
    uk: {
      loginTitle: "Вхід в Edge B.R.O.",
      loginUsername: "Ім'я користувача",
      loginPassword: "Пароль",
      loginSubmit: "Увійти",
      loginError: "Неправильне ім'я користувача або пароль",
      logoutButton: "Вийти",
      // ...
    }
  }
  ```

- [ ] **Step 2: Create Login Component**
  Create `frontend/src/components/Login.tsx`. Provide a beautiful dark design with inputs for username/password, and handling form submission to `POST /api/auth/login`. On success, call an `onLoginSuccess()` callback.
  Apply transitions: `className="animate-fade-in"` / `className="animate-modal-in"`.

- [ ] **Step 3: Create AdminsTab Component**
  Create `frontend/src/components/AdminsTab.tsx`. Renders a table of administrators. Includes "Create Admin" button and "Edit/Delete" actions. Shows "Comment" field. Integrates dynamic modals for creating/editing users. Uses transition animations.

- [ ] **Step 4: Update App.tsx logic**
  Modify `frontend/src/App.tsx` to handle authentication check:
  - Add state `user` (containing user profile or null) and `isAuthenticated`.
  - On mount, check if authenticated by querying `/api/auth/me`. If it returns `401`, set `isAuthenticated = false` and show the `<Login />` component.
  - Implement top-right profile header dropdown showing logged-in username, with options to "Edit Profile" (modal) and "Sign Out" (POST to `/api/auth/logout` and reload).
  - If `user?.is_superadmin` is true, render an "Admins" tab option, loading the `<AdminsTab />` component.

- [ ] **Step 5: Run Production Build**
  Run: `npm run build` inside `frontend/` to ensure typescript compilation passes without lint or build errors.

- [ ] **Step 6: Commit**
  Run: `git add frontend/src/`
  Commit message: `feat(frontend): implement login screen, profile settings dropdown, and user administration tab`

---

### Task 6: Documentation and Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `README_ru.md`

**Interfaces:**
- Consumes: None
- Produces: Updated configurations and updated reference guides.

- [ ] **Step 1: Update .env.example**
  Add configuration parameters to `.env.example`:
  ```bash
  SUPERADMIN_USERNAME=admin
  ADMIN_PASSWORD=q1w2e3r4
  JWT_SECRET_KEY=generate_a_random_jwt_secret_key_string
  ```

- [ ] **Step 2: Update README guides**
  Update `README.md` and `README_ru.md` documentation:
  - Document the default admin username (`admin`) and password (`q1w2e3r4`).
  - Explain how `SUPERADMIN_USERNAME` and `ADMIN_PASSWORD` seed the database on initial start.
  - Explain that once seeded, database updates take precedence over `.env` changes.
  - Explain the recovery procedure (deleting the superadmin user from DB triggers re-seeding on restart).

- [ ] **Step 3: Run final verification suite**
  Run: `pytest backend/tests/`
  Expected: PASS

- [ ] **Step 4: Commit**
  Run: `git add .env.example README.md README_ru.md`
  Commit message: `docs(auth): update setup documentation and configurations for auth system`
