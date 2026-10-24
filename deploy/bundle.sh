#!/usr/bin/env bash
# Build the license server bundle from this checkout: server/ and deploy/ only (no app, no history, no .env,
# no backups), plus its SHA-256. Run on your own PC or in CI; the VPS never needs access to GitHub.
#   ./bundle.sh            -> dist/akeno-licensing-<version>-<commit>.tar.gz (+ .sha256)
set -euo pipefail
cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || { echo "run this inside the source checkout"; exit 1; }
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' server/akeno_server/__init__.py)
commit=$(git rev-parse --short=12 HEAD)
name="akeno-licensing-${version}-${commit}"
mkdir -p dist
# only committed files: a local .env, keys or backups can never end up in the bundle
git archive --format=tar.gz --prefix="${name}/" -o "dist/${name}.tar.gz" HEAD server deploy
(cd dist && sha256sum "${name}.tar.gz" > "${name}.tar.gz.sha256")
echo "dist/${name}.tar.gz"
cat "dist/${name}.tar.gz.sha256"
