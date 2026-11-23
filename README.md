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
  services/          chat engine, question matching, knowledge search, AI answers,
                     ML classifier, settings, notifications, audit logging
  api/               JSON APIs: admin/ (Admin Control Panel), chat.py (AI Chat), me.py (own account)
  main/              student pages (dashboard, AI Chat) + public media
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
| Deliver due notifications once | `python -m flask notifications dispatch` |
| Keep delivering (separate terminal) | `python -m flask notifications worker` |

## Notifications and reminders

Scheduled notifications and event reminders are delivered when they come due. Any of these will do it:

- `flask notifications worker`, running in its own terminal (checks every 60 seconds),
- `flask notifications dispatch`, run every minute by Windows Task Scheduler or cron,
- the **Process due now** button on the Notifications page,
- automatically (at most every 30 seconds) whenever someone opens their inbox.

Email reminders need SMTP settings in `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM`, `SMTP_TLS`). Without them, email reminders are delivered in-app only.

## AI Chat

Students and staff use AI Chat at `/chatbot`. Each message is answered from the first source that fits:

1. **Configured questions & answers** (Admin > Questions & answers): a clicked suggestion or follow-up,
   or the best-matching question when its score reaches *Match strictness* (Assistant settings).
2. **Knowledge base**: a matching published entry (when *Answer from the knowledge base* is on).
3. **AI answer** (when *AI answers* are on): Claude answers using only relevant knowledge entries,
   near-matching configured answers and upcoming events. If they don't cover the question it says so,
   and the fallback is used instead of a guess.
4. **Fallback answer**, flagged for review.

Every message and reply is stored (`chat_sessions`, `chat_messages`) with how it was answered.
Admins review them at **Admin > Conversations** (`monitoring.read`):

- **Overview**: questions per day, how they were answered, most asked questions, most asked questions
  *without* an answer, and the configured answers and knowledge entries used most.
- **All conversations**: search and filter every conversation, and open its transcript to see how each
  answer was chosen.
- **Needs review**: questions that got the fallback answer. *Answer this question…* turns one into a
  configured question (`chatconfig.write`) so AI Chat answers it from then on; *Mark as reviewed* needs
  `monitoring.write`.

To enable AI answers, put the key in `.env` under the variable named in Assistant settings
(default `ANTHROPIC_API_KEY=...`), restart the server, switch *AI answers* on and click **Test connection**
(it sends one tiny request with the saved settings). The key is only read
on the server; it is never stored in the database or sent to the browser.

## Database

Local development uses SQLite at `instance/schoolsync.db`. For Supabase/PostgreSQL, install
`psycopg[binary]` and set `DATABASE_URL` in `.env` to the Supabase connection string, then run
`flask db upgrade` and `flask seed`.

## Authentication & access control

- Sign in at `/auth/login`. Admins land on `/admin/`, regular users on `/dashboard`.
- Accounts are created by administrators in **Admin > Users** (or `flask create-admin` for the first super admin).
- Everyone can edit their own name, email and password at **My profile** (user menu, top right). Changing the
  email or password needs the current password and signs out the account's other devices.
- Roles: `super_admin`, `admin`, `content_manager`, `viewer`, `user` (see `PERMISSIONS`/`ROLES` in `app/seeds.py`).
- Every `/admin/*` page and `/api/admin/*` endpoint requires an admin-capable account. Individual routes add
  `@permission_required("module.write")` from `app/auth/security.py`.
- Security rules are set in **Admin > System settings > Security** (needs the `settings.security` permission,
  which only `super_admin` has by default): lockout after N wrong passwords (default 5, for 15 minutes),
  the idle session timeout (default 8 hours) and the minimum password length (default 8).
- Sessions idle for longer than the timeout are signed out. The notification bell's background polling is sent
  with an `X-Background-Request` header so it doesn't keep an idle session alive.
- Deactivating a user or changing their password signs them out of every browser immediately.
- Logins, failures, lockouts, sign-outs and denied access attempts are written to `activity_logs`.

## Security notes

- Secrets (SECRET_KEY, AI API keys) live only in `.env` / server environment, never in the database or frontend.
- Passwords are hashed with scrypt (Werkzeug). All POST requests require a CSRF token.
- Login is rate-limited to 10 attempts per minute per IP; sending AI Chat messages to 20 per minute.
