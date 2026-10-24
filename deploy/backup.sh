#!/usr/bin/env bash
# Make a backup now (database dump + signing keys) and copy it to ./backups on this host (mode 700).
# The keys are secret: keep these files safe and off public storage.
set -euo pipefail
cd "$(dirname "$0")"
docker compose exec -T akeno-backup /bin/sh /backup/backup-loop.sh once
umask 077
mkdir -p backups
chmod 700 backups
container=$(docker compose ps -q akeno-backup)
latest=$(docker compose exec -T akeno-backup sh -c 'cat /backups/last-backup.json')
db=$(echo "$latest" | sed -E 's/.*"database": "([^"]+)".*/\1/')
keys=$(echo "$latest" | sed -E 's/.*"keys": "([^"]+)".*/\1/')
docker cp "$container:/backups/$db" "backups/$db"
docker cp "$container:/backups/$keys" "backups/$keys"
echo "Copied backups/$db and backups/$keys"
