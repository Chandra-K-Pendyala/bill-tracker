# Deploying to funprojects

The tracker runs on the Ubuntu 24.04 server funprojects with Docker Compose, from `/opt/bill-tracker`, next to the recipe website. It is published at <https://bills.whobrokeprod.com> through its own Cloudflare Tunnel.

Nothing opens a port: the app listens only on `127.0.0.1:3020` on the server, and the tunnel connector makes outgoing connections to Cloudflare. Two locks protect it: Cloudflare Access covers the whole site (an emailed one-time code), then the tracker's own sign-in.

Commands marked **On the Mac** run in the project folder on the Mac (`~/Chandra-projects/bill_tracker`). The rest run on the server as root over SSH.

## What runs

| Container | What it is |
| --- | --- |
| `bill-tracker-db-1` | PostgreSQL 17 with the bills database (volume `bill-tracker_postgres_data`). |
| `bill-tracker-app-1` | The Django app under gunicorn, on `127.0.0.1:3020` (uploads in volume `bill-tracker_uploads`). |
| `bill-tracker-cloudflared-1` | The Cloudflare Tunnel connector. It starts only after the tunnel token is set. |

Watchtower on the server leaves these containers alone (label `com.centurylinklabs.watchtower.enable=false`); updates go through `scripts/deploy.sh`.

## First deployment

### 1. Check the server

On the server:

```bash
docker compose version
ss -ltn | grep ':3020 ' || echo "port 3020 is free"
git --version
```

**Success:** a Compose version, "port 3020 is free", and a git version.

### 2. Get the code

The code is in the public repository <https://github.com/Chandra-K-Pendyala/bill-tracker>. It holds no `.env`, passwords, bills, backups or uploads; those exist only on the server. Cloning a public repository needs no key or password.

On the server:

```bash
git clone https://github.com/Chandra-K-Pendyala/bill-tracker.git /opt/bill-tracker
cd /opt/bill-tracker
git log -1 --oneline
```

**Success:** the same latest commit as on GitHub.

### 3. Create the server's settings

On the server:

```bash
cd /opt/bill-tracker
cp deploy/production.env.example .env
scripts/init-secrets.sh
```

The template already holds the server settings: the hostname bills.whobrokeprod.com, HTTPS behind Cloudflare, port 3020, and backups in `/var/backups/bill-tracker`. `init-secrets.sh` replaces the placeholder passwords with random ones and makes `.env` readable only by root.

**Success:** "Wrote new secrets to .env." Then save a copy of `.env` in your password manager: rebuilding the server needs it.

### 4. Start the app

On the server:

```bash
cd /opt/bill-tracker
scripts/up.sh
grep ADMIN_PASSWORD .env
```

**Success:** "Up: the app and its database answer on http://127.0.0.1:3020." On this first start the app creates the `admin` account with the `ADMIN_PASSWORD` shown. The database has no bills until step 7.

### 5. Protect the whole site with Cloudflare Access

Do this before the tunnel, so the site is never reachable without the email code.

In the Cloudflare dashboard, open Zero Trust: Access controls, Applications, Add an application, Self-hosted.

1. Name it "Bill tracker".
2. Add the public hostname `bills.whobrokeprod.com` and leave the path empty, so the policy covers every page.
3. Set the session duration to 24 hours.
4. Add the same policy as the cookbook's admin application (Allow, your email address), and keep the One-time PIN login method.
5. Save.

**Success:** "Bill tracker" is listed under Applications.

### 6. Create the tunnel

In Cloudflare: Networking, Tunnels, Create a tunnel, type Cloudflared, name "bill-tracker". On the install page, copy the token from the Docker command (the long string after `--token`). Do not paste it anywhere else.

On the server:

```bash
cd /opt/bill-tracker
scripts/set-tunnel-token.sh
docker compose up -d
docker compose logs cloudflared | grep "Registered tunnel connection"
```

Then open the tunnel `bill-tracker`, go to its Routes tab (not the global Networking, Routes page), and add a published application route: subdomain `bills`, domain `whobrokeprod.com`, service type HTTP, URL `app:8000`.

**Success:** "Token saved (... characters) and the tunnel is switched on.", then four "Registered tunnel connection" lines, and the route listed in the tunnel.

### 7. Sign in and load your bills

On the Mac, open <https://bills.whobrokeprod.com>. Cloudflare asks for your email and sends a code. Then sign in as `admin` with the password from step 4, and change it under Settings.

1. **Properties → Add property**, once for each home, with its address and whether it is owned or rented.
2. **Payments → Import bills**: choose every PDF in one bills folder and press Import. Repeat for each folder.

Each bill becomes that month's record with its PDF attached. A bill for an account the tracker doesn't know yet creates the account at the property with the same street address, and newer bills mark the earlier ones paid. A bill that is already recorded is reported and skipped, so a duplicate copy is harmless.

**Success:** the dashboard shows each property with this year's bills, and the open balance matches the bills you haven't paid yet.

To move the Mac's database instead of importing again (for example, after recording payments by hand on the Mac), make a backup on the Mac, copy the two files to the server, and follow "Restore a backup" below.

### 8. Test from your phone

On mobile data (not home Wi-Fi), open <https://bills.whobrokeprod.com>. Cloudflare asks for your email and sends a code, then the tracker asks you to sign in.

**Success:** the dashboard loads over https.

### 9. Stop the Mac copy

On the Mac:

```bash
docker compose stop
```

From now on the server holds your bills; import new bills at <https://bills.whobrokeprod.com>. The Mac keeps its volumes as an old copy. Don't run both and enter bills in each.

## Updates

Changes are pushed to GitHub from the Mac first. Then, on the server:

```bash
/opt/bill-tracker/scripts/deploy.sh
```

It pulls the latest commit, rebuilds the image, starts the stack, waits until it is healthy and checks the app. Migrations run when the app starts. Git never touches `.env`, the database or uploads.

**Success:** "Deploying <commit>: <message>", then "Up: the app and its database answer on http://127.0.0.1:3020."

## Backups

A systemd timer runs `scripts/backup.sh` every night at 03:50 (plus up to 10 minutes), after the cookbook's 03:30 backup. It writes `bill_tracker-db-<time>.sql.gz` and `bill_tracker-uploads-<time>.tar.gz` to `/var/backups/bill-tracker` and keeps the newest 14 of each. The folder is readable by root only. Backups stay on the server, so a failed disk would take them too: copy one to the Mac now and then.

### Set up the nightly backup (once)

On the server:

```bash
cd /opt/bill-tracker
cp deploy/systemd/bill-tracker-backup.service deploy/systemd/bill-tracker-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bill-tracker-backup.timer
systemctl list-timers bill-tracker-backup.timer
```

**Success:** the timer is listed with its next run at 03:50.

### Run a backup now

On the server:

```bash
systemctl start bill-tracker-backup.service
journalctl -u bill-tracker-backup.service -n 5 --no-pager
ls -lh /var/backups/bill-tracker
```

**Success:** "Backup complete." in the journal and two new files in the listing.

### Get an alert when a backup fails (optional)

In Uptime Kuma (port 3001 on the server): Add New Monitor, type Push, name "Bill tracker backup", heartbeat interval 90000 seconds (25 hours), tick the ntfy notification, and Save. Copy the push token: the part after `/api/push/` in the push URL, up to the `?`.

On the server:

```bash
cd /opt/bill-tracker
sed -i "s#^BACKUP_PUSH_URL=.*#BACKUP_PUSH_URL=http://127.0.0.1:3001/api/push/<push token>#" .env
systemctl start bill-tracker-backup.service
```

**Success:** the monitor turns green. A failed backup, or a night without one, then sends an alert.

### Copy a backup to the Mac

On the Mac:

```bash
scp root@funprojects:/var/backups/bill-tracker/<backup file> ~/Documents/
```

### Restore a backup

This replaces the live database and uploads with a backup; anything changed since that backup is lost. It asks you to type `replace` first, and stops the app.

On the server:

```bash
cd /opt/bill-tracker
ls -1t /var/backups/bill-tracker
scripts/restore.sh --replace /var/backups/bill-tracker/bill_tracker-db-<time>.sql.gz /var/backups/bill-tracker/bill_tracker-uploads-<time>.tar.gz
scripts/up.sh
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Cloudflare error 1033 or HTTP 530 | The tunnel connector is not running | `docker compose ps`, then `docker compose logs cloudflared` |
| Access says "That account does not have access" | Your email is not in the Access policy, or the One-time PIN login is off | Zero Trust: check the policy's email, and Integrations, Identity providers |
| "Bad Request (400)" | The hostname is not in `ALLOWED_HOSTS` | `ALLOWED_HOSTS` in `.env` must include `bills.whobrokeprod.com`, then `scripts/up.sh` |
| "CSRF verification failed" when saving a form | The https origin is not trusted | `CSRF_TRUSTED_ORIGINS` in `.env` must be `https://bills.whobrokeprod.com`, then `scripts/up.sh` |
| "Too many redirects" | `SECURE_SSL` is true but `TRUST_X_FORWARDED_PROTO` is not | Set `TRUST_X_FORWARDED_PROTO=true` in `.env`, then `scripts/up.sh` |
| `up.sh` says the stack did not become healthy | The app failed to start | `docker compose logs app` |
