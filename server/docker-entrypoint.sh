#!/bin/sh
# The API service applies database migrations before it starts; web and admin wait for it (depends_on).
set -eu
if [ "$#" -gt 0 ]; then
    exec "$@"                      # a one-off command (docker compose run akeno-api akeno-server ...)
fi
if [ "${AKENO_ROLE:-api}" = "api" ] || [ "${AKENO_ROLE:-api}" = "all" ]; then
    akeno-server migrate
fi
exec uvicorn akeno_server.main:get_app --factory --host 0.0.0.0 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips "${AKENO_FORWARDED_ALLOW_IPS:-127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}" \
    --no-server-header
