# GEMINI AI Manifest & Repository Rules

## Tech Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic, Celery, Ansible Runner
- Database: PostgreSQL
- Task Queue: Redis
- Frontend: React, TypeScript, Tailwind CSS, Lucide Icons
- Deployment: Docker Compose

## Coding Guidelines
- **Strict Python Type Hinting**: Always use Pydantic models for request/response serialization.
- **Maximum File Size**: No single file must exceed 500 lines. Split routers, tasks, and components when they grow.
- **Database Migrations**: Always use Alembic migrations for DB changes. Do not modify database schemas directly.
- **Secrets Management**: Read Borg Passphrase (`BORG_PASSPHRASE`) and Database credentials exclusively from environment variables/`.env`. Never store them in DB or VCS.
- **UI Styling & Animations**: All dropdown lists and modal windows MUST use CSS transition animations (e.g., `animate-modal-in`, `animate-dropdown-in`, `animate-fade-in`) to maintain the project's premium dynamic aesthetic. Fallback behavior must be ensured so that interfaces remain fully visible and usable in restricted environments (like the XFCE Live-CD) if hardware acceleration or animations are unsupported.
- **Multi-Language Support (i18n)**: All new features and UI text additions must support internationalization (English, Russian, Ukrainian). Keep text element lengths similar across languages to ensure the layout remains stable.


## Interaction & Communication Rules
- **Direct Question Answering**: When the user asks a question, answer it directly. Do not preemptively execute git pushes, modifications, or build actions unless explicitly requested or approved.
- **Non-Persistent Terminal for Stateless Commands**: Always run simple, stateless commands (e.g., `git status`, `git diff`, `git add`, `rm`, running tests, compilation, building) with `RunPersistent: false`. Only use `RunPersistent: true` when subsequent commands rely on shared shell variables or environment state. This prevents multiplexing locks and hanging.

