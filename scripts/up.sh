#!/usr/bin/env bash
# Build and start the stack, wait until it is healthy, and check that the app answers.
# Usage: scripts/up.sh   (on the server, or on the Mac)
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f .env ]; then
  echo "No .env in $(pwd). Create it first (README, or docs/DEPLOY.md on the server)." >&2
  exit 1
fi
port=$({ grep -E '^APP_PORT=' .env || true; } | tail -n 1 | cut -d= -f2-)
port=${port:-8008}

docker compose build -q
if ! docker compose up -d --wait; then
  echo "The stack did not become healthy. Recent logs:" >&2
  docker compose logs --tail 40 >&2
  exit 1
fi
curl -fsS "http://127.0.0.1:$port/health/" > /dev/null
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
echo "Up: the app and its database answer on http://127.0.0.1:$port."
