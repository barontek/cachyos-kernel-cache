# cachyos-kernel-cache

Builds the CachyOS kernel pinned in `barontek/nixos-config`'s `flake.lock` on
GitHub Actions and pushes it to a personal Cachix binary cache, so
`nixos-rebuild` substitutes the kernel instead of compiling it locally.

Background: the upstream binary cache (`attic.xuyh0120.win`, run together with
the Hydra CI of [xddxdd/nix-cachyos-kernel]) went down on 2026-10-15 after a
compromise of the maintainer's servers ([xddxdd/nix-cachyos-kernel#110]). This
repo maintains a replacement cache we control ourselves.

## What gets built

From `github:xddxdd/nix-cachyos-kernel/<rev>` at the exact revision pinned in
nixos-config, so the resulting store paths match the system 1:1 (verified via
`outPath`):

- the kernel and all its outputs (`out`, `dev`, `modules`),
- `linux-config-*`, a reference of the kernel `dev` output that is needed when
  building out-of-tree kernel modules and is not on `cache.nixos.org`.

NVIDIA / Tuxedo kernel modules are **not** built here (they depend on the whole
nixos-config). After a kernel update, expect a short local rebuild of those
(~15–40 min) unless this repo is extended to build them too.

## One-time setup

1. Create a public cache named `barontek` on [app.cachix.org](https://app.cachix.org)
   (free tier, 5 GB).
2. Create an auth token with write access to that cache, then add it to this
   repo as the `CACHIX_AUTH_TOKEN` secret:

   ```bash
   gh secret set CACHIX_AUTH_TOKEN --repo barontek/cachyos-kernel-cache
   ```

3. In nixos-config, replace the dead `lantian` substituter with this cache:

   ```nix
   nix.settings = {
     substituters = [ ... "https://barontek.cachix.org" ];
     trusted-public-keys = [ ... "barontek.cachix.org-1:<public key from Cachix>" ];
   };
   ```

## Kernel updates

```bash
cd ~/nixos-config
nix flake update nix-cachyos-kernel   # bump the kernel input
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
  "github:xddxdd/nix-cachyos-kernel/$rev#legacyPackages.x86_64-linux.linuxPackages-linux-cachyos-latest-x86_64-v3.kernel^*")
nix run nixpkgs#cachix -- push barontek $paths
```

[xddxdd/nix-cachyos-kernel]: https://github.com/xddxdd/nix-cachyos-kernel
[xddxdd/nix-cachyos-kernel#110]: https://github.com/xddxdd/nix-cachyos-kernel/issues/110
