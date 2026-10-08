#!/usr/bin/env bash
# Update the server to the latest commit on GitHub and restart (docs/DEPLOY.md, "Updates").
# Run it on the server:  /opt/bill-tracker/scripts/deploy.sh
# The server keeps its own .env, database and uploads; git never touches them (.gitignore).
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

git pull --ff-only
echo "Deploying $(git rev-parse --short HEAD): $(git log -1 --format=%s)"
exec scripts/up.sh
