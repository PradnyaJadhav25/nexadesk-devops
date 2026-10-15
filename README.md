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

`.github/workflows/ci.yml` runs on pushes to any branch, pull requests, and manual dispatch. Its checks job installs dependencies once and runs Pytest, Ruff, Bandit, and `pip-audit`. The next job builds the production Dockerfile and scans that image with Trivy. Trivy fails the workflow for **HIGH** or **CRITICAL** OS/library vulnerabilities when a fix is available; image scan failures block deployment. Pull requests and non-production branches run these checks but never deploy. Only pushes/manual runs on `main` or `master` can deploy.

## Render deployment

`render.yaml` describes a Render Docker web service using the existing `DOCKERFILE.dockerfile`. Render's health probe and the GitHub post-deployment check both use `/health`. The Blueprint disables Render's push-triggered auto-deploy so that production deployments happen only after the GitHub checks and Trivy scan pass.

### One-time Render setup

1. Create or sign in to a Render account and connect the GitHub repository.
2. In the Render dashboard, choose **New > Blueprint**, select this repository, and apply `render.yaml`. Choose the `starter` service plan; the attached 1 GB disk mounted at `/data` is needed to persist SQLite data. Render generates `SECRET_KEY` for the service; do not copy it into the repository.
3. Complete the initial service provisioning in Render. From the service's **Settings**, copy the service ID (`srv-...`) and the public service URL (for example, `https://your-service.onrender.com`). Confirm **Auto-Deploy** is disabled.
4. Create a Render API key in your account settings.
5. In GitHub, open **Settings > Environments**, create an environment named `production`, then add these two **environment secrets**:

   | Secret | Value |
   |---|---|
   | `RENDER_API_KEY` | The Render API key |
   | `RENDER_SERVICE_ID` | The Render service ID (`srv-...`) |

   Then open **Settings > Secrets and variables > Actions > Variables** and add `RENDER_EXTERNAL_URL` with the public HTTPS base URL, without `/health`. It is a public endpoint, not a credential. Keep credentials in Render/GitHub secret settings; never put them in `.env.example`, workflow YAML, or commits. The Flask session key is generated and kept in Render.

The `Deploy Application` job calls the Render API for the exact Git commit, waits for Render's deployment status to become `live`, and fails if Render reports a failed/canceled deployment or does not become live within 30 minutes. The following job retries the public `/health` endpoint for up to two minutes; any non-success HTTP response or response other than `{"status":"healthy"}` fails the workflow. To add an approval gate, configure required reviewers for the GitHub `production` environment.

### Verify a deployment

1. Run locally before pushing:

   ```powershell
   python -m pytest -q
   ruff check app.py tests
   bandit -q -r app.py
   pip-audit -r requirements.txt
   docker build --file DOCKERFILE.dockerfile --tag nexadesk:local .
   ```

2. Push a change to `main` (or `master`) or merge a pull request. In GitHub, open **Actions > NexaDesk CI/CD** and confirm checks, the Docker build, Trivy, deployment, and health-check jobs pass in order. If a check fails, deployment is blocked; inspect that job's log before retrying.
3. Open `https://your-service.onrender.com/health`. A healthy response is HTTP `200` with `{"status":"healthy"}`. The endpoint returns HTTP `503` if the database cannot be reached.
4. For runtime monitoring, use the Render service dashboard's events, logs, and metrics. Render also probes `/health`; a failed probe is visible in service events and can prevent an unhealthy instance from receiving traffic. The GitHub health-check result verifies each deployment, but does not replace continuous external uptime monitoring.

### SQLite persistence and limitations

The Render Blueprint mounts a persistent disk at `/data` and stores the SQLite file there, so deploys/restarts retain it. Render persistent disks require a paid service plan; confirm current Render pricing before provisioning. A disk-backed SQLite service must remain a single instance and is not suitable for horizontal scaling or high availability. Back up the database regularly. The Compose named volume remains unchanged for local development. If you need multiple instances or stronger managed backups, migrate to a managed PostgreSQL database before scaling.

## Originality and inherited code / licensing

The prior Portuguese workspace/task screens, routes, stylesheet, and browser scripts were removed and replaced with a new service-desk information architecture, wording, UI, model, and workflows. NexaDesk retains only generic technical foundations suitable for this stack: Flask, Flask-SQLAlchemy, Werkzeug password hashing, SQLite, and the existing Python/container deployment context. There are no copied third-party UI assets; the interface uses original markup and styling (with optional Google Fonts loaded by the browser).

The existing `LICENSE` file is retained and identifies the project as MIT-licensed, with the original copyright notice. Review that notice and MIT attribution obligations before redistributing this rewritten project. The pre-existing tracked `database/tarefas.db` is intentionally left untouched and is not read or migrated by NexaDesk; back it up and assess its data separately before removing it.

## Limitations

- This is a single-workspace learning project with SQLite, not a high-availability or multi-tenant service.
- Account provisioning is administrator-managed; password reset, invitations/email, and account deletion are not implemented.
- `init-db` creates the current schema but there are no Alembic migrations or automated legacy data migration.
- The interface loads its optional typefaces from Google Fonts when network access is available; system fonts are used as a fallback.
