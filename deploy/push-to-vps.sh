#!/usr/bin/env bash
# Update the license server on the VPS from your own PC over SSH: build the bundle (or use one from CI),
# copy it, and run update.sh there. The VPS gets no GitHub credentials at all.
#   ./push-to-vps.sh user@vps [/opt/akeno-licensing] [bundle.tar.gz]
set -euo pipefail
cd "$(dirname "$0")"
target="${1:?usage: push-to-vps.sh user@host [remote-folder] [bundle.tar.gz]}"
remote="${2:-/opt/akeno-licensing}"
bundle="${3:-}"
if [ -z "$bundle" ]; then
    bundle=$(./bundle.sh | head -n 1)
    bundle="../${bundle}"
fi
[ -f "$bundle" ] || { echo "no bundle at $bundle"; exit 1; }
hash=$(sha256sum "$bundle" | cut -d' ' -f1)
name=$(basename "$bundle")
ssh "$target" "mkdir -p '$remote/incoming'"
scp "$bundle" "$target:$remote/incoming/$name"
ssh -t "$target" "cd '$remote/deploy' && ./update.sh --bundle '../incoming/$name' --sha256 '$hash'"
