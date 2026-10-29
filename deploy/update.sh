#!/usr/bin/env bash
# Update the license server: back up, put the new server files in place, rebuild, restart.
# Migrations run on start. The database, the signing keys (Docker volumes), .env and backups/ are kept.
#
#   ./update.sh --bundle FILE [--sha256 HASH]   new version from a bundle (bundle.sh / push-to-vps.sh / CI)
#   ./update.sh --rollback                      back to the server version before the last update
#   ./update.sh --git                           git pull first (only with a read-only deploy key, see README)
#   ./update.sh                                 rebuild and restart the files that are here
set -euo pipefail
cd "$(dirname "$0")"
root=$(cd .. && pwd)
bundle="" expected="" mode="local"
while [ $# -gt 0 ]; do
    case "$1" in
        --bundle) bundle="$2"; mode="bundle"; shift 2 ;;
        --sha256) expected="$2"; shift 2 ;;
        --rollback) mode="rollback"; shift ;;
        --git) mode="git"; shift ;;
        --no-pull) shift ;;                          # older option: nothing is pulled anyway
        *) echo "unknown option $1"; exit 2 ;;
    esac
done

check_bundle() {
    local file="$1"
    [ -f "$file" ] || { echo "no bundle at $file"; exit 1; }
    if [ -n "$expected" ]; then
        echo "$expected  $file" | sha256sum -c - >/dev/null || { echo "SHA-256 does not match: not installed"; exit 1; }
    elif [ -f "$file.sha256" ]; then
        (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null) \
            || { echo "SHA-256 does not match: not installed"; exit 1; }
    else
        echo "no SHA-256 given (--sha256 or $file.sha256): not installed"; exit 1
    fi
    # only server/ and deploy/ below one top folder; no absolute paths, no .., no links
    local bad
    bad=$(tar -tvzf "$file" | awk '{print substr($1,1,1) " " $NF}' | while read -r kind path; do
        rest="${path#*/}"
        case "$kind" in l|h) echo "link: $path"; continue ;; esac
        case "$path" in /*|*../*|*/..|..*) echo "path: $path"; continue ;; esac
        case "$rest" in ""|server|server/*|deploy|deploy/*) ;; *) echo "outside: $path" ;; esac
    done)
    [ -z "$bad" ] || { echo "refused bundle:"; echo "$bad"; exit 1; }
}

case "$mode" in
    bundle)
        check_bundle "$bundle"
        ./backup.sh
        staging=$(mktemp -d "$root/.update.XXXXXX")
        tar -xzf "$bundle" -C "$staging" --strip-components=1 --no-same-owner
        rm -rf "$root/.previous" && mkdir -p "$root/.previous"
        cp -a "$root/server" "$root/.previous/server"
        cp -a "$root/deploy" "$root/.previous/deploy"
        # new code in place; what belongs to this VPS stays: deploy/.env, deploy/backups/, incoming/ (never in a
        # bundle, so never overwritten)
        rm -rf "$root/server" && mv "$staging/server" "$root/server"
        (cd "$staging/deploy" && find . -mindepth 1 -maxdepth 1 -exec cp -a {} "$root/deploy/" \;)
        rm -rf "$staging"
        ;;
    rollback)
        [ -d "$root/.previous/server" ] || { echo "no previous version kept"; exit 1; }
        ./backup.sh
        # The previous server must not run on a newer database schema: take the database back to the previous
        # version's newest migration first, with the newer code that knows how (0002 -> 0001 withdraws unused
        # module codes, which the older server would otherwise treat as AKENO Full codes).
        newest_migration() { ls "$1" 2>/dev/null | sed -n 's/^\([0-9][0-9][0-9][0-9]\)_.*\.py$/\1/p' | sort | tail -1; }
        previous_head=$(newest_migration "$root/.previous/server/migrations/versions")
        current_head=$(newest_migration "$root/server/migrations/versions")
        if [ -n "$previous_head" ] && [ "$previous_head" != "$current_head" ]; then
            echo "Taking the database back from migration $current_head to $previous_head"
            docker compose run --rm akeno-api akeno-server migrate-down "$previous_head"
        fi
        rm -rf "$root/server" && cp -a "$root/.previous/server" "$root/server"
        (cd "$root/.previous/deploy" && find . -mindepth 1 -maxdepth 1 ! -name .env ! -name backups \
            -exec cp -a {} "$root/deploy/" \;)
        ;;
    git)
        ./backup.sh
        git -C "$root" pull --ff-only
        ;;
    local)
        ./backup.sh
        ;;
esac
cd "$root/deploy"
docker compose build
docker compose up -d
docker compose ps
