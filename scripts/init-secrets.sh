#!/usr/bin/env bash
# Give .env fresh random secrets and apply them to an existing database.
#
#   scripts/init-secrets.sh           only if .env still has placeholder values
#   scripts/init-secrets.sh --force   replace the secrets even if they were already set
#
# Creates .env from .env.example when it is missing, then replaces SECRET_KEY, POSTGRES_PASSWORD,
# DATABASE_URL and ADMIN_PASSWORD. When the database volume already exists, Postgres and the admin
# account are changed to the new passwords too, so the db service must be running.
set -euo pipefail
umask 077
cd "$(cd "$(dirname "$0")/.." && pwd)"

[ -f .env ] || cp .env.example .env
if [ "${1:-}" != "--force" ] && ! grep -q 'replace-with' .env; then
  echo ".env already has its own secrets. Run with --force to replace them."
  exit 0
fi

get() { grep -E "^$1=" .env | head -n 1 | cut -d= -f2-; }
gen() { python3 -c 'import secrets, sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"; }
db_user=$(get POSTGRES_USER)
db_name=$(get POSTGRES_DB)
secret_key=$(gen 50)
db_password=$(gen 24)
admin_password=$(gen 18)

new_env=$(mktemp .env.new.XXXXXX)
trap 'rm -f "$new_env"' EXIT
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    SECRET_KEY=*) echo "SECRET_KEY=$secret_key" ;;
    POSTGRES_PASSWORD=*) echo "POSTGRES_PASSWORD=$db_password" ;;
    DATABASE_URL=*) echo "DATABASE_URL=postgresql://$db_user:$db_password@db:5432/$db_name" ;;
    ADMIN_PASSWORD=*) echo "ADMIN_PASSWORD=$admin_password" ;;
    *) echo "$line" ;;
  esac
done < .env > "$new_env"

project=$(docker compose config --format json | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')
existing_db=false
if docker volume inspect "${project}_postgres_data" >/dev/null 2>&1; then
  existing_db=true
  if ! docker compose ps --status running --services | grep -qx db; then
    echo "The database already exists. Start it first (docker compose up -d db), then run this again." >&2
    exit 1
  fi
  # Postgres reads POSTGRES_PASSWORD only when it creates the volume, so change the password in place.
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U "$db_user" -d "$db_name" \
    -c "ALTER USER \"$db_user\" WITH PASSWORD '$db_password'" >/dev/null
  echo "Changed the database password."
fi

mv "$new_env" .env
chmod 600 .env
echo "Wrote new secrets to .env."

if $existing_db; then
  # The admin account was created with the old ADMIN_PASSWORD; set it to the new one.
  docker compose run --rm --no-deps app python manage.py create_admin --reset-password
fi
echo "Done. Sign in as $(get ADMIN_USERNAME); the password is ADMIN_PASSWORD in .env."
