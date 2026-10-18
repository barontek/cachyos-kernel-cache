# cachyos-kernel-cache

Builds the CachyOS kernel pinned in `barontek/nixos-config`'s `flake.lock` on
GitHub Actions and pushes it to a personal Cachix binary cache, so
`nixos-rebuild` substitutes the kernel instead of compiling it locally.

Background: the upstream binary cache (`attic.xuyh0120.win`, run together with
the Hydra CI of [xddxdd/nix-cachyos-kernel]) went down on 2026-10-15 after a
compromise of the maintainer's servers ([xddxdd/nix-cachyos-kernel#110]). This
repo maintains a replacement cache we control ourselves.

## What gets built

From `github:barontek/cachyos-kernel-cache/<rev>` — this repo — at the exact
revision pinned in nixos-config, so the resulting store paths match the
system 1:1 (verified via `outPath`):

- the kernel and all its outputs (`out`, `dev`, `modules`),
- `linux-config-*`, a reference of the kernel `dev` output that is needed when
  building out-of-tree kernel modules and is not on `cache.nixos.org`.

NVIDIA / Tuxedo kernel modules are **not** built here (they depend on the whole
nixos-config). After a kernel update, expect a short local rebuild of those
(~15–40 min) unless this repo is extended to build them too.

## Recipe

The Nix recipe lives in this repo at the root (flake, `kernel-cachyos/`,
`zfs-cachyos/`, `update.py`); it is vendored from
[xddxdd/nix-cachyos-kernel] at `e61f03b6` (GPL-2.0, see `LICENSE`). The
kernel, config, patch and ZFS pins are `kernel-cachyos/version.json` and
`zfs-cachyos/version.json`.

They are refreshed daily by the `Update kernel pins` workflow, which runs
`nix run .#update-cachyos` (fetches the newest from the CachyOS repos and
re-hashes) and commits the result — the same thing upstream's own automation
did. To bump manually, run that command and commit. The recipe's own nixpkgs
pin is deliberately left to manual updates.

## One-time setup

1. Create a public, **self-signed** cache named `barontek` on
   [app.cachix.org](https://app.cachix.org) (free tier, 5 GB). Self-signed
   means the signing key never leaves machines you control: neither Cachix nor
   a leaked auth token alone can publish paths your systems would trust.
2. Create an auth token with write access to the cache, store it locally and
   generate the signing keypair (kept in `~/.config/cachix/cachix.dhall`):

   ```bash
   nix run nixpkgs#cachix -- authtoken --stdin   # paste token, then Ctrl-D
   nix run nixpkgs#cachix -- generate-keypair barontek
   ```

3. Store both secrets in this repo:

   ```bash
   gh secret set CACHIX_AUTH_TOKEN --repo barontek/cachyos-kernel-cache
   gh secret set CACHIX_SIGNING_KEY --repo barontek/cachyos-kernel-cache
   ```

4. In nixos-config, replace the dead `lantian` substituter with this cache:

   ```nix
   nix.settings = {
     substituters = [ ... "https://barontek.cachix.org" ];
     trusted-public-keys = [ ... "barontek.cachix.org-1:<public key of the generated keypair>" ];
   };
   ```

## Kernel updates

The pins refresh automatically (see "Recipe"), so an update is just:

```bash
cd ~/nixos-config
nix flake update nix-cachyos-kernel   # relock the kernel input
# ... commit & push the config as usual ...
cd ~/cachyos-kernel-cache
./build.sh                            # dispatch CI for the new revision
```

Wait for the workflow to go green (~3–5 h; reruns are cheap, an already cached
kernel is skipped), then `sudo nixos-rebuild switch` — the kernel comes from
the cache. If you rebuild before CI finishes, Nix compiles the kernel locally.

GitHub Actions is free for this repo (public, standard runners); the cache is
public, effectively serving kernels to anyone running the same revision.

## Fallback

If CI fails or exceeds the 6 h job limit, build once locally and push:

```bash
nix run nixpkgs#cachix -- authtoken <TOKEN>   # once per machine
rev=$(sed -n '/"nix-cachyos-kernel"/,$p' ~/nixos-config/flake.lock | grep -m1 '"rev"' | cut -d'"' -f4)
paths=$(nix build --no-link --print-out-paths \
  "github:barontek/cachyos-kernel-cache/$rev#legacyPackages.x86_64-linux.linux-cachyos-latest-x86_64-v3^*")
nix run nixpkgs#cachix -- push barontek $paths
```

On the machine where `generate-keypair` was run, pushes are signed
automatically; on any other machine the signing key must be present in the
local Cachix config too.

[xddxdd/nix-cachyos-kernel]: https://github.com/xddxdd/nix-cachyos-kernel
[xddxdd/nix-cachyos-kernel#110]: https://github.com/xddxdd/nix-cachyos-kernel/issues/110
