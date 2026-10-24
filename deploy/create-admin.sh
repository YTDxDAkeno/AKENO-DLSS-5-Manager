#!/usr/bin/env bash
# Create the first super admin (works once, while none exists). The password is asked for on the terminal and
# never appears in the shell history or the process list.
set -euo pipefail
cd "$(dirname "$0")"
email=${1:?usage: create-admin.sh <e-mail address> [name]}
docker compose exec akeno-api akeno-server create-super-admin --email "$email" --name "${2:-}"
