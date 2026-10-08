#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh into this stack.
#   scripts/restore.sh [--replace] <database-backup.sql.gz> [uploads-backup.tar.gz]
# Into an empty database (a new install) it just restores. If the database has data, --replace wipes it
# first, after you type "replace"; that stops the app, so start it again with scripts/up.sh.
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

usage() { echo "Usage: scripts/restore.sh [--replace] <database-backup.sql.gz> [uploads-backup.tar.gz]" >&2; exit 2; }
replace=false
if [ "${1:-}" = "--replace" ]; then replace=true; shift; fi
[ $# -ge 1 ] && [ $# -le 2 ] || usage
db_backup=$1
uploads_backup=${2:-}

for f in "$db_backup" ${uploads_backup:+"$uploads_backup"}; do
  if [ ! -f "$f" ]; then echo "No such file: $f" >&2; exit 1; fi
  if ! gzip -t "$f" 2>/dev/null; then echo "$f is not a valid gzip file." >&2; exit 1; fi
done

if ! docker compose ps --status running --services | grep -qx db; then
  echo "Start the database first: docker compose up -d db" >&2
  exit 1
fi

# Runs psql in the db container as its own POSTGRES_USER on POSTGRES_DB (expanded inside the container).
psql_db() { docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' psql "$@"; }

tables=$(psql_db -tAc "select count(*) from information_schema.tables where table_schema = 'public'")
if [ "$tables" != "0" ]; then
  if ! $replace; then
    echo "The database already has data. Run again with --replace to wipe it and restore the backup." >&2
    exit 1
  fi
  echo "This deletes everything in the database and replaces it with the backup."
  answer=""
  if ! read -r -p "Type replace to continue: " answer < /dev/tty || [ "$answer" != "replace" ]; then
    echo "Nothing was changed."
    exit 1
  fi
  docker compose stop app
  psql_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
fi

gunzip -c "$db_backup" | psql_db > /dev/null  # errors still reach stderr and stop the restore
echo "Restored the database from $db_backup."

if [ -n "$uploads_backup" ]; then
  docker compose run --rm --no-deps -T app tar -C /app/media -xzf - < "$uploads_backup"
  echo "Restored the uploaded files from $uploads_backup."
fi

echo "Now in the database: $(psql_db -tAc "select count(*) from tracker_billpayment") bills," \
  "$(psql_db -tAc "select count(*) from tracker_billaccount") accounts," \
  "$(psql_db -tAc "select count(*) from tracker_attachment") attachments."
echo "Start the app with: scripts/up.sh"
