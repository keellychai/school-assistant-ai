# SchoolSync AI

AI-based school administration and communication assistant, with an Admin Control Panel
that manages events, notifications, knowledge, AI Chat questions/answers and assistant behaviour.

## Project layout

```
app/
  __init__.py        create_app() factory
  config.py          settings from environment variables (.env)
  extensions.py      db, migrate, login_manager, csrf, limiter
  models/            SQLAlchemy models (users/RBAC, events, notifications, chat config,
                     knowledge, settings, chat history, activity logs)
  services/          knowledge search, ML classifier, audit logging
  main/              existing student pages + /api/ask
  seed_data/         sample content imported once by `flask seed`
  seeds.py, cli.py   `flask seed`, `flask create-admin`
  templates/, static/
migrations/          Alembic migrations (Flask-Migrate)
tests/               pytest suite
data/, models/, train_model.py   training data + TF-IDF classifier
run.py               entry point
```

## First-time setup (Windows, PowerShell)

Keep the virtual environment **outside OneDrive**. OneDrive locks and syncs package files,
which breaks installs.

```powershell
py -3.14 -m venv $HOME\.venvs\schoolsync
& $HOME\.venvs\schoolsync\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you get `DLL load failed ... An Application Control policy has blocked this file`
(Windows Smart App Control blocks SQLAlchemy's compiled extensions), reinstall SQLAlchemy as pure Python:

```powershell
$env:DISABLE_SQLALCHEMY_CEXT = "1"
pip install --force-reinstall --no-deps --no-binary sqlalchemy SQLAlchemy==2.1.9
Remove-Item Env:DISABLE_SQLALCHEMY_CEXT
```

Then configure the app and create the database:

```powershell
Copy-Item .env.example .env      # then fill in SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
$env:FLASK_APP = "run.py"
python -m flask db upgrade       # create / update tables
python -m flask seed             # roles, permissions, settings, sample content, first admin
python run.py                    # http://127.0.0.1:5000
```

## Everyday commands

| Task | Command |
|---|---|
| Run the app | `python run.py` |
| Run tests | `python -m pytest -q` |
| Create another admin | `python -m flask create-admin` |
| After changing a model | `python -m flask db migrate -m "describe change"` then `python -m flask db upgrade` |
| Retrain the classifier | `python train_model.py` |

## Database

Local development uses SQLite at `instance/schoolsync.db`. For Supabase/PostgreSQL, install
`psycopg[binary]` and set `DATABASE_URL` in `.env` to the Supabase connection string, then run
`flask db upgrade` and `flask seed`.

## Authentication & access control

- Sign in at `/auth/login`. Admins land on `/admin/`, regular users on `/dashboard`.
- Accounts are created by administrators (`flask create-admin` for now; the User Management module comes later).
- Roles: `super_admin`, `admin`, `content_manager`, `viewer`, `user` (see `PERMISSIONS`/`ROLES` in `app/seeds.py`).
- Every `/admin/*` page and `/api/admin/*` endpoint requires an admin-capable account. Individual routes add
  `@permission_required("module.write")` from `app/auth/security.py`.
- After 5 wrong passwords (configurable in system settings) an account is locked for 15 minutes.
- Deactivating a user or changing their password signs them out of every browser immediately.
- Logins, failures, lockouts, sign-outs and denied access attempts are written to `activity_logs`.

## Security notes

- Secrets (SECRET_KEY, AI API keys) live only in `.env` / server environment, never in the database or frontend.
- Passwords are hashed with scrypt (Werkzeug). All POST requests require a CSRF token.
- Login is rate-limited to 10 attempts per minute per IP; `/api/ask` to 30 per minute.
