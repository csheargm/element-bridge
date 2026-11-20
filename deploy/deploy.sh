#!/usr/bin/env bash
# Deploy Hagency Rooms to the public VM, from GitHub only. Run on the operator's computer:
#   deploy/deploy.sh [COMMIT]        (default: the tip of origin/master)
# COMMIT must be on csheargm/hagency-rooms's master (checked here and again on the VM against
# GitHub). The VM (ssh host $HAGENCY_VM, default hagency-vm) checks it out in ~/hagency/app, copies
# its files to ~/hagency/pm for @pm, runs bootstrap.sh, then this checks the public URLs. Nothing
# but the commit id is sent; secrets are made on the VM and never come back.
set -euo pipefail
cd "$(dirname "$0")/.."
VM="${HAGENCY_VM:-hagency-vm}"
REPO_URL="https://github.com/csheargm/hagency-rooms"
ROOMS="${ROOMS_DOMAIN:-rooms.hagencylabs.xyz}" MATRIX="${MATRIX_DOMAIN:-matrix.hagencylabs.xyz}"
SERVER="${SERVER_NAME:-$MATRIX}"

git fetch -q "$REPO_URL" master
master=$(git rev-parse FETCH_HEAD)
sha=$(git rev-parse --verify -q "${1:-$master}^{commit}") || { echo "✗ unknown commit: ${1:-}"; exit 2; }
git merge-base --is-ancestor "$sha" "$master" || { echo "✗ $sha is not on GitHub's master: refusing"; exit 2; }
echo "Deploying $sha to $VM"

ssh "$VM" bash -s -- "$sha" "$REPO_URL" <<'REMOTE'
set -euo pipefail
sha=$1 repo=$2 app=~/hagency/app pm=~/hagency/pm
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "✗ not a commit id"; exit 2; }
[ -d "$app/.git" ] || git clone -q "$repo" "$app"
git -C "$app" fetch -q origin master
git -C "$app" merge-base --is-ancestor "$sha" origin/master || { echo "✗ $sha is not on master: refusing"; exit 2; }
[ -z "$(git -C "$app" status --porcelain --untracked-files=no)" ] || { echo "✗ code was edited on the server: refusing"; exit 2; }
if [ -f "$app/deploy/.env" ]; then                     # @pm stops before its files change
  (cd "$app/deploy" && sudo docker compose --env-file .env stop pm >/dev/null 2>&1) || true
fi
old=$(cat "$pm/.deployed" 2>/dev/null || true)
git -C "$app" checkout -q --detach "$sha"
mkdir -p "$pm" && chmod 700 "$pm"
if [ -n "$old" ] && git -C "$app" cat-file -e "$old^{commit}" 2>/dev/null; then   # files the new commit dropped
  comm -23 <(git -C "$app" ls-tree -r --name-only "$old" | sort) <(git -C "$app" ls-tree -r --name-only "$sha" | sort) \
    | (cd "$pm" && xargs -r -d '\n' rm -f --)
fi
git -C "$app" archive "$sha" | tar -x -C "$pm"         # tracked files only: @pm's state stays
echo "$sha" > "$pm/.deployed"
"$app/deploy/bootstrap.sh"
REMOTE

echo "Checking from here:"
python3 deploy/health.py "$ROOMS" "$MATRIX" "$SERVER"
