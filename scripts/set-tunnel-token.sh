#!/bin/sh
# Asks for the Cloudflare Tunnel token without showing it, writes it into .env and
# turns the tunnel on (COMPOSE_PROFILES=tunnel). Run it in a terminal on the server:
#   /opt/bill-tracker/scripts/set-tunnel-token.sh
# Then start the tunnel with: docker compose up -d
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env in $(pwd). Create it first (docs/DEPLOY.md)." >&2
  exit 1
fi

printf 'Paste the tunnel token, then press Enter: '
if [ -t 0 ]; then
  # Hide what is pasted, and always turn echo back on, even after Ctrl-C.
  trap 'stty echo' EXIT INT TERM
  stty -echo
fi
read -r token
if [ -t 0 ]; then stty echo; fi
printf '\n'

# A token is one long base64 string; anything else means the wrong thing was pasted.
case "$token" in
  '' | *[!A-Za-z0-9+/=_-]*)
    echo "That does not look like a tunnel token. Nothing was changed." >&2
    exit 1
    ;;
esac
if [ "${#token}" -lt 100 ]; then
  echo "That is too short for a tunnel token (${#token} characters). Nothing was changed." >&2
  exit 1
fi

tmp=$(mktemp .env.XXXXXX)
awk -v t="$token" '
  /^CLOUDFLARE_TUNNEL_TOKEN=/ { print "CLOUDFLARE_TUNNEL_TOKEN=" t; token = 1; next }
  /^COMPOSE_PROFILES=/ { print "COMPOSE_PROFILES=tunnel"; profiles = 1; next }
  { print }
  END {
    if (!token) print "CLOUDFLARE_TUNNEL_TOKEN=" t
    if (!profiles) print "COMPOSE_PROFILES=tunnel"
  }
' .env > "$tmp"
chmod 600 "$tmp"
mv "$tmp" .env
echo "Token saved (${#token} characters) and the tunnel is switched on. Start it with: docker compose up -d"
