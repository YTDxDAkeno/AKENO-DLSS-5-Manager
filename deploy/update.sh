#!/usr/bin/env bash
# Update the licensing server: back up, rebuild from the current source, restart. Migrations run on start.
set -euo pipefail
cd "$(dirname "$0")"
./backup.sh
if [ -d ../.git ] && [ "${1:-}" != "--no-pull" ]; then
    git -C .. pull --ff-only
fi
docker compose build --pull
docker compose up -d
docker compose ps
