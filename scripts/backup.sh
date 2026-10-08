#!/usr/bin/env bash
# Back up the database and uploaded bills from the running Compose stack.
# Usage: scripts/backup.sh
# Settings come from the environment, then .env, then these defaults: BACKUP_DIR=backups, BACKUP_KEEP=14,
# BACKUP_PUSH_URL unset. When BACKUP_PUSH_URL (an Uptime Kuma push URL) is set, every run reports up or down.
set -euo pipefail
umask 077

cd "$(cd "$(dirname "$0")/.." && pwd)"
setting() { { [ -f .env ] && grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; } || true; }
BACKUP_DIR=${BACKUP_DIR:-$(setting BACKUP_DIR)}
BACKUP_DIR=${BACKUP_DIR:-backups}
KEEP=${BACKUP_KEEP:-${KEEP:-$(setting BACKUP_KEEP)}}
KEEP=${KEEP:-14}
PUSH_URL=${BACKUP_PUSH_URL:-$(setting BACKUP_PUSH_URL)}
PUSH_URL=${PUSH_URL%%\?*}
TS=$(date +%Y%m%d-%H%M%S)

push() {
  [ -n "$PUSH_URL" ] || return 0
  curl -fsS -m 10 -G "$PUSH_URL" --data-urlencode "status=$1" --data-urlencode "msg=$2" > /dev/null ||
    echo "Could not report to the push URL." >&2
}

partials=()
finished=false
cleanup() {
  for f in "${partials[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done
  $finished || push down "Bill tracker backup failed"
}
trap cleanup EXIT

running=$(docker compose ps --status running --services)
for service in db app; do
  if ! printf '%s\n' "$running" | grep -qx "$service"; then
    echo "The db and app services must be running (docker compose up -d)." >&2
    exit 1
  fi
done

mkdir -p "$BACKUP_DIR"

# save <final path> <command...>: stream the command's gzip output to a .partial
# file, verify it, then move it into place.
save() {
  local final=$1; shift
  local partial="$final.partial"
  partials+=("$partial")
  if ! "$@" > "$partial" || ! gzip -t "$partial"; then
    echo "Backup failed while writing $final" >&2
    exit 1
  fi
  mv "$partial" "$final"
  echo "Created $(du -h "$final" | cut -f1)	$final"
}

dump_db() {
  docker compose exec -T db sh -c 'pg_dump --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip
}
dump_uploads() {
  docker compose exec -T app tar -C /app/media -czf - .
}

save "$BACKUP_DIR/bill_tracker-db-$TS.sql.gz" dump_db
save "$BACKUP_DIR/bill_tracker-uploads-$TS.tar.gz" dump_uploads

# Keep the newest $KEEP of each kind; the timestamp in the name sorts lexically.
for kind in "db-*.sql.gz" "uploads-*.tar.gz"; do
  { ls -1 "$BACKUP_DIR"/bill_tracker-$kind 2>/dev/null || true; } | sort -r | tail -n +$((KEEP + 1)) |
    while read -r old; do
      rm -f "$old"
      echo "Pruned $old"
    done
done

finished=true
push up "OK"
echo "Backup complete."
