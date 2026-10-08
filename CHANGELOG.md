# Changelog

## 1.4.1 — 2026-10-08

### Fixed
- Bills now find their property even when the property's address was typed differently from the bill. After the street number and name, the tracker tries the unit or suite number (when several properties share a street), a street name typed a little differently with the same street number, and finally a postal code printed on the bill that only one property has.
- When a property is found by a similar street name or by postal code, the import message says so.
- When no property matches, the error lists your properties and their addresses, so the difference is easy to see.
- Tested on real bills with a home's address typed eight different ways (unit first, no space in the street name, a typo, the unit typed as the street number, no street number at all): every bill went to the right property each time.

## 1.4.0 — 2026-10-08

### Added
- Bills from companies that aren't in the provider list now import too. The tracker reads the company's name from the bill: the payee line ("Make cheques payable to…"), a government name ("City of…", "Municipality of…") or a business-style name ("… Inc.", "… Utilities", "… Co-op"), checked against the bill's web address.
- The tracker adds the company to your providers with a category guessed from the bill: from the company's name first, then the units billed, such as kWh or gallons. Its bills are flagged "Check".
- Later bills from that company find its account by account number, even after you rename the provider.
- With the known-provider list switched off, it named the right company and category on all 27 real bills from five companies used for testing.

### Changed
- "Read unrecognized bills as" on the import page is now only an override, for a bill whose company name can't be found.

## 1.3.0 — 2026-10-08

### Added
- Bill readers for Toronto Hydro and Wyse Meter Solutions (sub-metered water), built from real bills.
- A general reader for other major Ontario providers listed in `tracker/providers.py`: electricity distributors such as Alectra, Hydro Ottawa, Elexicon, London Hydro and Enova Power; city water bills; Bell, Rogers, Telus and other phone and internet companies; and Enercare and Reliance rentals. It finds the amount due, the bill date, the due date, the account number and the service address.
- Bills read by the general reader are flagged "Check" until you open and save them. On 27 real bills from five providers, it read every one of those fields correctly.
- "Read unrecognized bills as" on the import page: choose the provider for bills the tracker doesn't recognize, and the general reader reads them.
- A dashboard notice and a payment-list filter for bills that still need a check.
- Automatic withdrawals: when a bill says the provider will withdraw the payment (Toronto Hydro does), the bill is recorded as paid on that date and shows "Auto-pay scheduled" until then. The account is marked auto-pay.
- Usage in gallons, for Wyse water bills.

### Changed
- Each payment listed on a bill now goes to the earlier bill with exactly that amount, with its own date. A date assumed from an automatic withdrawal is corrected when the next bill shows the real one.
- A bill fully covered by a credit on the account is recorded as paid.
- When an earlier balance or a credit changes what a bill asks for, the record's notes say so.
- Mark paid also marks earlier unpaid bills of the same account paid, because a bill's total includes balances carried from earlier bills.
- The built-in provider list adds the major Ontario providers.

## 1.2.0 — 2026-10-07

### Added
- Server hosting on funprojects at https://bills.whobrokeprod.com, step by step in `docs/DEPLOY.md`. The site sits behind its own Cloudflare Tunnel, with Cloudflare Access covering every page.
- A Cloudflare Tunnel connector in `docker-compose.yml`. It starts only when `.env` sets `COMPOSE_PROFILES=tunnel`, which `scripts/set-tunnel-token.sh` does after asking for the token without showing it.
- `deploy/production.env.example` with the server settings, and a nightly backup timer in `deploy/systemd/`.
- `scripts/deploy.sh` updates the server: it pulls the latest commit from GitHub and restarts the stack. Git never touches `.env`, the database or uploads.
- `scripts/up.sh` builds and starts the stack, waits until it is healthy and checks the app.
- `scripts/restore.sh` restores a backup into a new install. With `--replace`, after you type `replace`, it wipes an existing database first.
- `/health/` answers without a login when the app can reach its database. The container health check and the deploy scripts use it.
- `TRUST_X_FORWARDED_PROTO` makes HTTPS requests from the tunnel count as secure, so `SECURE_SSL=true` works behind Cloudflare.
- Backups read their settings from `.env`, and report up or down to Uptime Kuma when `BACKUP_PUSH_URL` is set.

### Changed
- Containers have memory limits and log rotation, and opt out of Watchtower.

### Fixed
- gunicorn 26 logged a control-socket error at every start; the unused control socket is now off.

## 1.1.0 — 2026-10-07

### Added
- Properties. Each home is a property, marked owned or rented. Accounts belong to a property, and an account's service address is filled in from its property when left blank. The dashboard and the Properties page show billed, paid and open amounts per property.
- Bill import. Upload Hydro One, Enbridge Gas and Municipality of North Grenville water bill PDFs from **Payments → Import bills**, or run the `import_bills` management command. Each bill becomes the record for the month of its statement date, with the PDF attached.
- Import details: an account the tracker doesn't know yet is created and placed at the property with the same street address. Payments printed on a bill mark the earlier bill paid. A bill that is already recorded is skipped.
- Bill details: each record can hold the statement date, the service period and usage (kWh or m³).
- An account history page with charts of the amount and the usage on each bill.
- A **Mark paid** button on the dashboard, the reminders page, the payment list and the payment form.
- Bar charts for totals by category, provider and property, and hover values on line charts.
- `scripts/init-secrets.sh` creates `.env` with random secrets, and applies new passwords to an existing database.
- `scripts/backup.sh` saves a database dump and an archive of uploaded files into `backups/` and keeps the newest 14 of each.
- `create_admin --reset-password` sets an existing admin's password to `ADMIN_PASSWORD`.

### Changed
- Payment status is worked out from the amounts: paid, partially paid or unpaid. Overdue comes from the due date. The status filter now matches the status shown in the lists.
- The dashboard's open balance covers every unpaid bill, not only this month's. Charts and breakdowns use the amounts billed, and the monthly chart runs from the first month with bills to the current month.
- Reminders list each bill once: overdue, due in the next 7 days, or due in 8 to 30 days.
- CSV exports include the property, statement date and usage.
- Amounts use a comma as the thousands separator, for example $1,234.56.
- The app listens on 127.0.0.1 only by default. Set `APP_BIND` in `.env` to change this.
- The app refuses to start when `DEBUG` is false and `SECRET_KEY` is still a placeholder.
- Dependencies: Django 5.2.18 (a security release), gunicorn 26.2.0, psycopg 3.3.6 and whitenoise 6.12.0. The image adds poppler-utils to read PDFs.
- The app user in the image can no longer write to the application code. Bill PDFs and backups are kept out of the Docker build.

### Fixed
- Uploaded files are deleted from disk when their payment, account or attachment is deleted.
- Category names display properly, for example "Natural gas" instead of "Natural_Gas".
- Invalid filter values in the address bar no longer cause a server error.
- Action links in tables line up with the rest of their row.
- The attachment download test closed the test database connection, so the tests that ran after it failed. It now reads the file instead.
