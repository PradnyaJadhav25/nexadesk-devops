# NexaDesk

NexaDesk is a small, self-hosted IT service desk for teams that need one clear place to request help, manage a support queue, and keep a record of decisions. It is designed as an explainable college DevOps project: a Flask application, relational models, server-rendered pages, and one persistent SQLite database.

## Product capabilities

- Dashboard with live counts for all tickets, each workflow status, and a ticket-backed priority distribution.
- Ticket creation with title, description, category, priority, requester, and optional support-agent assignment.
- Search, status/category/priority filters, sorting, and 10-item pagination.
- Ticket detail view with comments, status and assignment controls, timestamps, and an activity timeline.
- Administrator, support-agent, and requester roles. Requesters see only their own tickets; support agents can work tickets; administrators can manage accounts.
- First-run administrator setup, secure password hashing, CSRF checks for every form submission, input validation, and clear error messages.
- Responsive light interface, request logging in JSON, and a database health endpoint.

## Architecture

```text
Browser
  └── Flask routes and server-rendered Jinja templates (app.py)
      ├── Flask-Login sessions and role checks
      ├── SQLAlchemy models: User, Ticket, Comment, Activity
      └── SQLite file (instance/nexadesk.db by default)
```

There are no frontend frameworks or external service dependencies. `app.py` keeps the application factory, models, and routes together to keep the project easy to explain. Templates, CSS, browser behavior, and tests are separated by purpose. Gunicorn serves the production container; Flask's built-in server is only used if `python app.py` is chosen for local development.

## Requirements and local setup

- Python 3.12+
- pip
- Docker Compose (optional)

Create a virtual environment and install the app and development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Initialize the SQLite schema and start the local development server:

```powershell
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:DATABASE_URL = "sqlite:///nexadesk.db"
flask --app app:create_app init-db
python app.py
```

Open <http://127.0.0.1:5000>. The first visit redirects to `/setup`, where the first account becomes administrator. After setup, administrators can add people and assign their roles from **People**. No demo accounts or sample ticket data are seeded.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | Random per process in local development | Flask session signing key. Set a long, random value in production; production startup fails if it is missing. |
| `DATABASE_URL` | `sqlite:///instance/nexadesk.db` | SQLAlchemy database URL. For a persistent container volume, use `sqlite:////data/nexadesk.db`. |
| `APP_ENV` | unset | Set to `production` to enforce a configured `SECRET_KEY`. |
| `COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS so session cookies use the Secure flag. |
| `WEB_CONCURRENCY` | `2` in Docker | Number of Gunicorn worker processes. |

Local databases live in the ignored `instance/` directory. The `init-db` command creates tables; the project does not yet include a schema migration framework. Back up the SQLite file before replacing the application or changing models.

## Docker

Copy `.env.example` to `.env`, replace its placeholder with a generated secret, then build and run:

```powershell
Copy-Item .env.example .env
# Edit .env and set SECRET_KEY to a unique random value.
docker compose up --build
```

Open <http://localhost:5000>. Compose persists SQLite data in the named `nexadesk-data` volume and the image runs Gunicorn as a non-root user. To stop the service without removing data, run `docker compose down`. `docker compose down -v` removes the database volume and its contents.

The production image can also be built directly:

```powershell
docker build --file DOCKERFILE.dockerfile --tag nexadesk:local .
```

## Tests and quality checks

```powershell
python -m pytest -q
ruff check app.py tests
bandit -q -r app.py
pip-audit -r requirements.txt
```

The tests use an isolated in-memory SQLite database. They cover setup/login, password hashing, ticket creation and validation, visibility boundaries, role restrictions, status transitions, comments/activity, search/filter/sorting/pagination, CSRF enforcement, and `/health`.

## CI workflow

`.github/workflows/ci.yml` runs on pushes to `main`/`master`, pull requests, and manual dispatch. It installs Python dependencies; runs pytest, Ruff, Bandit, and pip-audit; and builds the Docker image. The CI image build is separate from the test/security job so the container build is checked on every trigger.

## Originality and inherited code / licensing

The prior Portuguese workspace/task screens, routes, stylesheet, and browser scripts were removed and replaced with a new service-desk information architecture, wording, UI, model, and workflows. NexaDesk retains only generic technical foundations suitable for this stack: Flask, Flask-SQLAlchemy, Werkzeug password hashing, SQLite, and the existing Python/container deployment context. There are no copied third-party UI assets; the interface uses original markup and styling (with optional Google Fonts loaded by the browser).

The existing `LICENSE` file is retained and identifies the project as MIT-licensed, with the original copyright notice. Review that notice and MIT attribution obligations before redistributing this rewritten project. The pre-existing tracked `database/tarefas.db` is intentionally left untouched and is not read or migrated by NexaDesk; back it up and assess its data separately before removing it.

## Limitations

- This is a single-workspace learning project with SQLite, not a high-availability or multi-tenant service.
- Account provisioning is administrator-managed; password reset, invitations/email, and account deletion are not implemented.
- `init-db` creates the current schema but there are no Alembic migrations or automated legacy data migration.
- The interface loads its optional typefaces from Google Fonts when network access is available; system fonts are used as a fallback.
