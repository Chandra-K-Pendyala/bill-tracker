# Bill Payment Tracker

A self-hosted, responsive bill and payment tracker for Canadian households. It includes local authentication, built-in Canadian providers, custom providers, recurring account records, monthly payment history, reminders, KPI reports, charts, protected attachments, and CSV exports.

## Stack

- Django 5.2 and Gunicorn
- PostgreSQL 17
- Server-rendered HTML/CSS and dependency-free canvas charts
- Docker Compose with persistent database and upload volumes

All application routes require login. Passwords use Django's secure password hashers, POST forms use CSRF tokens, records are owner-scoped, and uploaded bills are available only through an authenticated download endpoint. The app never stores provider website passwords.

## Quick start

Requirements: Docker Engine with Docker Compose v2.

1. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. Replace `SECRET_KEY`, `POSTGRES_PASSWORD`, and `ADMIN_PASSWORD`. Generate a secret with:

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
   ```

   Keep the password in `DATABASE_URL` synchronized with `POSTGRES_PASSWORD`. URL-encode special characters used in that URL.
   If port 8000 is already used, set `APP_PORT` to another host port and update `CSRF_TRUSTED_ORIGINS` to match.

3. Build and start:

   ```bash
   docker compose up -d --build
   docker compose ps
   docker compose logs -f app
   ```

4. Visit [http://localhost:8008](http://localhost:8008) and sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`.

The initial admin is created on the first start. Later container starts never overwrite its password. Change it from **Settings** after signing in.

For access from another device on your LAN, add the server hostname/IP to `ALLOWED_HOSTS`, add the full origin (for example `http://192.168.1.10:8000`) to `CSRF_TRUSTED_ORIGINS`, then restart the app:

```bash
docker compose up -d
```

## Using the app

1. **Providers:** search the seeded Canadian provider directory. Choose **Add custom provider** when a service is missing. Seeded providers are locked; only your custom providers can be edited or deleted.
2. **Accounts:** add the account number, provider-associated email, service address, frequency, typical amount, due day, and auto-pay state. A newly created custom provider appears immediately in this form.
3. **Payments:** add one record per account and billing month. Track due/paid amounts, dates, status, payment method, reference, notes, and an optional PDF/image attachment up to 10 MB.
4. **Dashboard and reminders:** see current month/year totals, outstanding bills, trends, categories, top providers, upcoming bills, and overdue bills.
5. **Reports:** filter by month, year, provider, category, status, province, or address. Export the filtered payment history as CSV. Accounts have a separate CSV export.

## Persistence and backups

Compose creates `postgres_data` and `uploads` named volumes. Normal restarts and container recreation preserve both. `docker compose down -v` intentionally deletes them.

Back up the database:

```bash
docker compose exec -T db pg_dump -U bill_tracker bill_tracker > bill_tracker_backup.sql
```

Restore into an empty database:

```bash
docker compose exec -T db psql -U bill_tracker bill_tracker < bill_tracker_backup.sql
```

List the volume names before arranging a filesystem-level upload backup:

```bash
docker volume ls | grep bill_tracker
```

## Updates and administration

Apply a new version:

```bash
docker compose up -d --build
docker compose logs --tail=100 app
```

Migrations and provider seeding run idempotently at app startup. To create another administrator:

```bash
docker compose exec app python manage.py createsuperuser
```

Stop without deleting data:

```bash
docker compose down
```

## Verification by phase

These commands mirror the implementation phases and can be run after changes:

```bash
# 1–2: project and Compose configuration
docker compose config
docker compose build

# 3: schema and migrations
docker compose run --rm app python manage.py migrate --check
docker compose run --rm app python manage.py makemigrations --check --dry-run

# 4–8: authentication, provider/account/payment workflows
docker compose run --rm app python manage.py test tracker

# 9–11: dashboard, reports, charts, exports (framework checks)
docker compose run --rm app python manage.py check

# 12: running service health
docker compose up -d
docker compose ps
curl -I http://localhost:8008/login/
```

For local development without Docker, Python 3.10+ is required:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_providers
python manage.py createsuperuser
python manage.py runserver
```

SQLite is selected automatically when `DATABASE_URL` is absent, making this local workflow lightweight. Docker always uses PostgreSQL.

To validate Compose before creating `.env`, use `ENV_FILE=.env.example docker compose config`.

## Security notes

- Do not commit `.env`; it is ignored by Git.
- Use a unique admin password and a long random `SECRET_KEY`.
- Put the service behind HTTPS (Caddy/Nginx or a trusted VPN) before exposing it beyond a trusted LAN.
- Restrict access to the Docker host and include both database and upload volumes in backups.
- Attachment extensions are allow-listed, size-limited, renamed on disk, and downloaded only after an ownership check.

## Project layout

```text
bill_tracker/        Django configuration
tracker/             models, forms, views, routes, migrations, tests, commands
templates/           responsive application pages
static/              CSS and local chart renderer
Dockerfile           application image
docker-compose.yml   app, PostgreSQL, and persistent volumes
entrypoint.sh        migrations, seed, admin bootstrap, and Gunicorn startup
```
