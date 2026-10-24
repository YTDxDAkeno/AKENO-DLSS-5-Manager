#!/bin/sh
# Runs in the akeno-backup container: a database dump and the signing keys every BACKUP_INTERVAL_HOURS,
# the newest BACKUP_KEEP of each kept. "backup-loop.sh once" makes one backup and exits (backup.sh uses it).
set -eu
umask 077
OUT=/backups
mkdir -p "$OUT"

one() {
    stamp=$(date -u +%Y%m%d-%H%M%S)
    pg_dump --format=custom --no-owner --file="$OUT/akeno-db-$stamp.dump.part"
    mv "$OUT/akeno-db-$stamp.dump.part" "$OUT/akeno-db-$stamp.dump"
    tar -czf "$OUT/akeno-keys-$stamp.tar.gz.part" -C /keys .
    mv "$OUT/akeno-keys-$stamp.tar.gz.part" "$OUT/akeno-keys-$stamp.tar.gz"
    for kind in akeno-db akeno-keys; do
        ls -1t "$OUT"/$kind-* 2>/dev/null | tail -n +$((${BACKUP_KEEP:-14} + 1)) | xargs -r rm -f
    done
    size=$(wc -c < "$OUT/akeno-db-$stamp.dump")
    printf '{"finished_at": "%s", "database": "akeno-db-%s.dump", "database_bytes": %s, "keys": "akeno-keys-%s.tar.gz"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stamp" "$size" "$stamp" > "$OUT/last-backup.json.part"
    mv "$OUT/last-backup.json.part" "$OUT/last-backup.json"
    echo "backup $stamp done ($size bytes)"
}

if [ "${1:-}" = "once" ]; then
    one
    exit 0
fi
until pg_isready -q; do sleep 3; done
while true; do
    one || echo "backup failed; retrying in an hour" >&2
    sleep $(( ${BACKUP_INTERVAL_HOURS:-24} * 3600 ))
done
