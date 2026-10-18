#!/usr/bin/env bash
# Dispatch the kernel build for a recipe revision via GitHub Actions, so it
# lands in the Cachix cache "barontek".
#
# Normally this isn't needed: "Update kernel pins" refreshes the pins daily,
# dispatches the build itself, and a successful build relocks ~/nixos-config.
# Use this to retry a failed build or to build a specific revision.
#
# Usage:
#   ./build.sh              # build the current recipe revision (repo HEAD)
#   ./build.sh <revision>   # build a specific recipe revision
set -euo pipefail

repo="barontek/cachyos-kernel-cache"
workflow="kernel.yml"

rev="${1:-$(gh api "repos/$repo/commits/main" --jq .sha)}"
[[ "$rev" =~ ^[0-9a-f]{40}$ ]] || {
  echo "error: invalid revision '$rev'" >&2
  exit 1
}

echo "Dispatching build of cachyos-kernel-cache@$rev"
gh workflow run "$workflow" --repo "$repo" -f "rev=$rev"
sleep 5
gh run list --repo "$repo" --workflow "$workflow" --limit 1
