#!/usr/bin/env bash
# Restore a backup made by backup.sh: ./restore.sh backups/akeno-db-<stamp>.dump [backups/akeno-keys-<stamp>.tar.gz]
# Replaces the current database (and keys, if given). The services are stopped meanwhile.
set -euo pipefail
cd "$(dirname "$0")"
dump=${1:?usage: restore.sh <database dump> [keys archive]}
keys=${2:-}
[ -f "$dump" ] || { echo "No such file: $dump"; exit 1; }
read -r -p "This replaces the current license database. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }
./backup.sh || echo "Could not back up the current state first; continuing."
trap 'echo "Restore failed; starting the services again." >&2; docker compose up -d' ERR
docker compose stop akeno-api akeno-web akeno-admin
docker compose up -d akeno-database
db_container=$(docker compose ps -q akeno-database)
docker cp "$dump" "$db_container:/tmp/restore.dump"
docker compose exec -T akeno-database sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/restore.dump; rm -f /tmp/restore.dump'
if [ -n "$keys" ]; then
    # root reads the owner-only archive; the keys then belong to the service user again (mode 600)
    docker compose run --rm --no-deps -T --user 0 --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER -v "$(realpath "$keys"):/restore/keys.tar.gz:ro" akeno-api python -c "
import os, pathlib, shutil, tarfile
keys = pathlib.Path('/data/keys')
for old in keys.iterdir():
    old.unlink()
with tarfile.open('/restore/keys.tar.gz') as archive:
    archive.extractall(keys, filter='data')
for path in [keys, *keys.iterdir()]:
    shutil.chown(path, 'akeno', 'akeno')
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
print('signing keys restored:', sorted(p.name for p in keys.iterdir()))
"
fi
docker compose up -d
echo "Restored. Check: docker compose exec akeno-api akeno-server check"
