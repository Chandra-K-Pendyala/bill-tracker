# Bill Payment Tracker

A self-hosted, responsive bill and payment tracker for Canadian households. It groups accounts by property (owned or rented), reads Hydro One, Enbridge Gas and North Grenville water bill PDFs into payment records, and includes local authentication, built-in Canadian providers, custom providers, monthly payment history, reminders, KPI reports, charts, protected attachments, and CSV exports.

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version. To run it on the funprojects server at https://bills.whobrokeprod.com, follow [docs/DEPLOY.md](docs/DEPLOY.md).

## Stack

- Django 5.2 LTS and Gunicorn
- PostgreSQL 17
- Server-rendered HTML/CSS and dependency-free canvas charts
- poppler-utils (`pdftotext`) to read bill PDFs
- Docker Compose with persistent database and upload volumes

All application routes require login. Passwords use Django's secure password hashers, POST forms use CSRF tokens, records are owner-scoped, and uploaded bills are available only through an authenticated download endpoint. The app never stores provider website passwords.

## Quick start

Requirements: Docker Engine with Docker Compose v2, and `python3` on the host (used only to generate secrets).

1. Create `.env` with random secrets:

   ```bash
   scripts/init-secrets.sh
   ```

   It copies `.env.example` and fills in `SECRET_KEY`, `POSTGRES_PASSWORD`, `DATABASE_URL` and `ADMIN_PASSWORD`. On an install whose database already exists, it also changes the database and admin passwords to match; start the database first with `docker compose up -d db`. Run it with `--force` to replace secrets that are already set.

2. Build and start:

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

3. Open [http://localhost:8008](http://localhost:8008) and sign in as `admin`. The password is `ADMIN_PASSWORD` in `.env`:

   ```bash
   grep ADMIN_PASSWORD .env
   ```

   Change it under **Settings** after signing in. Later container starts never overwrite it; `docker compose exec app python manage.py create_admin --reset-password` sets it back to `ADMIN_PASSWORD`.

The app listens on this computer only (127.0.0.1). To use it from another device on a trusted home network, set `APP_BIND=0.0.0.0` in `.env`, add the computer's address to `ALLOWED_HOSTS` (for example `192.168.1.10`) and the full origin to `CSRF_TRUSTED_ORIGINS` (for example `http://192.168.1.10:8008`), then run `docker compose up -d`. The connection is plain HTTP, so don't do this on a network you don't trust.

## Using the app

1. **Properties:** add each home and mark it owned or rented.
2. **Import bills:** **Payments → Import bills**. Choose one or more PDFs (see [Supported bills](#supported-bills)). Choosing a property is only needed when a bill's account is new and its street address doesn't match a property.
3. **Accounts:** created by an import, or added by hand. Open an account to see its history and charts.
4. **Payments:** one record per account and billing month. Use **Mark paid** when you pay a bill. Bills the import doesn't support can be added by hand, with an optional PDF or image up to 10 MB.
5. **Dashboard and reminders:** open balance, bills due in the next 30 days, overdue bills, and totals by property, category and provider.
6. **Reports:** filter by property, month, year, provider, category, status, province or address, and export the result as CSV. Accounts have a separate CSV export.

How the import fills in records:

- Each bill becomes the record for the month of its statement date. The amount is that bill's own charges; a balance carried forward stays on the earlier bill.
- Bills list the payments received since the previous bill. Each payment marks the earlier bill with exactly that amount paid, on its own date. North Grenville and Wyse bills don't print payment dates, so those show as paid with no date.
- When a bill says the provider will withdraw the payment (Toronto Hydro does), the bill is recorded as paid on that date and shows "Auto-pay scheduled" until then. A bill fully covered by a credit is recorded as paid.
- **Mark paid** also marks earlier unpaid bills of the same account paid, because a bill's total includes balances carried from earlier bills.
- A bill that's already recorded is skipped, so importing the same file twice is safe. Within one upload, bills are applied oldest first.
- Payment status comes from the amounts (paid, partially paid, unpaid). A bill that isn't paid by its due date shows as overdue.

## Supported bills

The tracker reads the text of bill PDFs; scanned bills without text can't be read.

- **Read exactly:** Hydro One, Toronto Hydro, Enbridge Gas, Wyse Meter Solutions and Municipality of North Grenville water.
- **Read generally:** about 55 other major Ontario providers listed in `tracker/providers.py`, such as Alectra, Hydro Ottawa, Elexicon, London Hydro, Enova Power, city water bills, Bell, Rogers and Telus. These bills are flagged "Check" until you open and save them.
- **Any other company:** the tracker reads the company's name from the bill and adds it to your providers, with a category guessed from the bill. Its bills are flagged "Check", and later bills find the account by its number even if you rename the provider. If a bill's company can't be found, choose it under "Read unrecognized bills as".

A sample bill from a provider is all that's needed to give it its own reader.

## Importing a folder of bills

Put the PDFs in a folder named `All Bills` in the project directory (subfolders are fine), then run:

```bash
docker compose run --rm -v "$PWD/All Bills:/import:ro" app python manage.py import_bills --user admin /import
```

Add `--dry-run` to read the bills and show what would be imported without saving anything. Bills are imported oldest first, and running the command again skips bills that are already recorded. The folder is mounted read-only and is never copied into the image.

## Backups

Save the database and the uploaded bills while the stack is running:

```bash
scripts/backup.sh
```

It writes `bill_tracker-db-<timestamp>.sql.gz` and `bill_tracker-uploads-<timestamp>.tar.gz` into `backups/` and keeps the newest 14 of each (set `KEEP` to change this). The files are readable only by you.

Restore into a new install (start its database first with `docker compose up -d db`):

```bash
scripts/restore.sh backups/bill_tracker-db-<timestamp>.sql.gz backups/bill_tracker-uploads-<timestamp>.tar.gz
scripts/up.sh
```

On a database that already has data, add `--replace`. It asks you to type `replace`, then wipes the database and restores the backup.

Backups read `BACKUP_DIR`, `BACKUP_KEEP` and `BACKUP_PUSH_URL` (an optional Uptime Kuma push URL) from the environment or `.env`.

Compose keeps the data in the `postgres_data` and `uploads` named volumes. Normal restarts and container recreation preserve both. `docker compose down -v` deletes them.

## Updates and administration

Apply a new version (builds the image, starts the stack, waits until it is healthy and checks the app):

```bash
scripts/up.sh
```

Migrations and provider seeding run idempotently at app startup. To create another administrator:

```bash
docker compose exec app python manage.py createsuperuser
```

Stop without deleting data:

```bash
docker compose down
```

## Verification

```bash
docker compose config --quiet
docker compose exec app python manage.py check
docker compose exec app python manage.py makemigrations --check --dry-run
docker compose exec app python manage.py test tracker
docker compose ps
curl http://localhost:8008/health/
```

`/health/` answers `ok` without a login when the app can reach its database.

The tests use their own temporary database and upload folder, so they don't touch your data.

## Local development without Docker

Requires Python 3.10 or newer (Django 5.2 doesn't run on the macOS system Python 3.9) and poppler for bill imports (`brew install poppler` on macOS).

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_providers
python manage.py createsuperuser
python manage.py runserver
```

SQLite is selected automatically when `DATABASE_URL` is absent, and `DEBUG` defaults to true outside Docker. Docker always uses PostgreSQL.

To validate Compose before creating `.env`, use `ENV_FILE=.env.example docker compose config`.

## Security notes

- `.env` holds the secrets. It is ignored by Git, kept out of the Docker build, and `scripts/init-secrets.sh` writes it readable only by you.
- The app refuses to start with `DEBUG=false` and a placeholder `SECRET_KEY`.
- By default the app is reachable only from this computer. Put it behind HTTPS (Caddy/Nginx or a trusted VPN) before exposing it beyond a trusted LAN.
- Imported bills are read with `pdftotext` in a time-limited subprocess, and must start with a PDF header and be 10 MB or smaller. Attachments added by hand are limited to PDF and image extensions and 10 MB.
- Attachments are renamed on disk, downloaded only after an ownership check, and deleted when their record is deleted.
- Include both the database and the uploads in backups (`scripts/backup.sh` does both).

## Project layout

```text
bill_tracker/        Django configuration (and the en-CA number format override)
tracker/             models, forms, views, routes, migrations, commands
tracker/bill_import.py   reading bill PDFs into payment records
tracker/tests/       tests and anonymized bill-text fixtures
templates/           responsive application pages
static/              CSS and local chart renderer
scripts/             init-secrets, up, backup, restore, deploy (server: pull and restart), set-tunnel-token
deploy/              server settings template and the nightly backup timer
docs/DEPLOY.md       runbook for the funprojects server
Dockerfile           application image
docker-compose.yml   app, PostgreSQL, persistent volumes, and the Cloudflare Tunnel connector (off unless enabled)
entrypoint.sh        migrations, seed, admin bootstrap, and Gunicorn startup
```
