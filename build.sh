#!/usr/bin/env bash
# Build the CachyOS kernel revision pinned in ~/nixos-config (flake.lock) via
# GitHub Actions, so it lands in the Cachix cache "barontek".
#
# Usage:
#   ./build.sh              # use the revision from ~/nixos-config/flake.lock
#   ./build.sh <revision>   # build a specific nix-cachyos-kernel revision
set -euo pipefail

repo="barontek/cachyos-kernel-cache"
workflow="kernel.yml"
config_dir="${NIXOS_CONFIG:-$HOME/nixos-config}"

rev="${1:-}"
if [[ -z "$rev" ]]; then
  lock="$config_dir/flake.lock"
  [[ -f "$lock" ]] || {
    echo "error: $lock not found; set NIXOS_CONFIG to point at the config repo" >&2
    exit 1
  }
  rev=$(sed -n '/"nix-cachyos-kernel"/,$p' "$lock" | grep -m1 '"rev"' | cut -d'"' -f4)
fi
[[ "$rev" =~ ^[0-9a-f]{40}$ ]] || {
  echo "error: invalid revision '$rev'" >&2
  exit 1
}

echo "Dispatching build of nix-cachyos-kernel@$rev"
gh workflow run "$workflow" --repo "$repo" -f "rev=$rev"
sleep 5
gh run list --repo "$repo" --workflow "$workflow" --limit 1
