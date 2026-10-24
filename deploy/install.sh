#!/usr/bin/env bash
# First installation of the AKENO licensing server on a VPS that may already run other projects.
# Safe to run again: existing secrets, data and keys are kept.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is required (https://docs.docker.com/engine/install/)."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose plugin is required."; exit 1; }

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example."
fi
chmod 600 .env

secret() { head -c 48 /dev/urandom | base64 | tr -d '\n/+=' | head -c 56; }
fill() {   # fill() NAME: set NAME in .env to a new random value if it is empty
    if grep -qE "^$1=\$" .env; then
        sed -i "s|^$1=\$|$1=$(secret)|" .env
        echo "Generated $1."
    fi
}
fill POSTGRES_PASSWORD
fill AKENO_TOKEN_SECRET
fill AKENO_CODE_PEPPER

ask() {    # ask NAME "question": ask for a value when NAME is empty and a terminal is attached
    if grep -qE "^$1=\$" .env && [ -t 0 ]; then
        read -r -p "$2 " value
        [ -n "$value" ] && sed -i "s|^$1=\$|$1=$value|" .env
    fi
}
ask AKENO_PUBLIC_URL "Public address of the license server (for example https://licenses.<your-domain>):"
ask AKENO_ADMIN_URL "Address of the admin website (for example https://admin.<your-domain>, empty: none):"

# --no-build: use an image built beforehand (docker build -t akeno-licensing:local ../server)
[ "${1:-}" = "--no-build" ] || docker compose build
docker compose up -d
echo "Waiting for the API to become healthy..."
for _ in $(seq 1 60); do
    state=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q akeno-api)" 2>/dev/null || true)
    [ "$state" = "healthy" ] && break
    sleep 2
done
docker compose exec -T akeno-api akeno-server keys init
echo
echo "Public keys for the Windows app build (repository variable AKENO_LICENSE_PUBLIC_KEYS):"
docker compose exec -T akeno-api akeno-server keys export --server-url "$(grep -E '^AKENO_PUBLIC_URL=' .env | cut -d= -f2-)"
echo
echo "Next steps:"
echo "  1. Add the two sites from Caddyfile.example to your Caddy and reload it (or: docker compose --profile caddy up -d)."
echo "  2. Create the super admin:  ./create-admin.sh you@example.org"
echo "  3. Sign in on the admin website, set up two-factor sign-in, then generate activation codes."
