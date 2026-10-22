#!/usr/bin/env python3
"""Delete old kernel store paths from the Cachix cache.

The Cachix CLI has no delete command.  The web dashboard deletes individual
store paths through

    DELETE /api/v1/cache/<cache>/<store-hash>.narinfo

which is what this script uses.  Dry-run is the default; pass --apply to
actually delete anything.

By default the kernel outputs for the revision pinned in
~/nixos-config/flake.lock and for this repository's origin/main are kept.
Every other kernel-related path in the cache is considered old.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://app.cachix.org/api/v1"
OWNER = "barontek"
REPO = "cachyos-kernel-cache"
CACHE = "barontek"
VARIANT = "linux-cachyos-latest-x86_64-v3"
FLAKE = f"github:{OWNER}/{REPO}"
SEARCH_LIMIT = 20  # Cachix's web API does not expose paging
USER_AGENT = f"{REPO}/1.0 (+https://github.com/{OWNER}/{REPO})"

STORE_PATH_RE = re.compile(r"/nix/store/([a-z0-9]{32})-[^\"'\s\\]+")
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:-rc\d+)?")
KERNEL_SUFFIX_RE = re.compile(
    r"^(?:"
    r"linux-cachyos.*"
    r"|linux-config-.*"
    r"|linux-src-patched"
    r"|config"
    r"|kernel-intermediate-config"
    r"|cachyos-.*\.tar\.gz"
    r")$"
)


def human_size(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def run(cmd, timeout=1800):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} failed with exit code {result.returncode}:\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def git(repo_dir, *args):
    return run(["git", "-C", str(repo_dir), *args]).strip()


def get_token():
    token = os.environ.get("CACHIX_AUTH_TOKEN")
    if token:
        return token

    config = Path.home() / ".config/cachix/cachix.dhall"
    if config.exists():
        match = re.search(r'authToken\s*=\s*"([^"]+)"', config.read_text())
        if match:
            return match.group(1)

    sys.exit(
        "error: no Cachix auth token found; set CACHIX_AUTH_TOKEN or run "
        "`cachix authtoken`"
    )


def api(token, path, method="GET"):
    url = API + path
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
        return json.loads(body) if body else None


def search_cache(token, cache, term):
    quoted = urllib.parse.quote(term, safe="")
    return api(token, f"/cache/{cache}/search/{quoted}") or []


def cache_details(token, cache):
    return api(token, f"/cache/{cache}/details")


def narinfo(cache, store_hash):
    url = f"https://{cache}.cachix.org/{store_hash}.narinfo"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def narinfo_exists(cache, store_hash):
    url = f"https://{cache}.cachix.org/{store_hash}.narinfo"
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def narinfo_references(text):
    if not text:
        return set()
    for line in text.splitlines():
        if line.startswith("References:"):
            return {path.rsplit("/", 1)[-1].split("-", 1)[0] for path in line.split()[1:]}
    return set()


def hashes_from_store_paths(paths):
    return {path.rsplit("/", 1)[-1].split("-", 1)[0] for path in paths}


def store_paths_in(value):
    if isinstance(value, str):
        yield from (match.group(0) for match in STORE_PATH_RE.finditer(value))
    elif isinstance(value, dict):
        for child in value.values():
            yield from store_paths_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from store_paths_in(child)


def config_pin(config_lock):
    try:
        data = json.loads(Path(config_lock).read_text())
    except (OSError, json.JSONDecodeError):
        return None

    nodes = data.get("nodes", {})
    node = nodes.get("nix-cachyos-kernel")
    if node is None:
        for candidate in nodes.values():
            locked = candidate.get("locked", {})
            if (
                locked.get("owner") == OWNER
                and locked.get("repo") == REPO
                and locked.get("rev")
            ):
                node = candidate
                break
    if node:
        return node.get("locked", {}).get("rev")
    return None


def default_keep_revs(repo_dir, config_lock):
    revs = []
    pinned = config_pin(config_lock)
    if pinned:
        revs.append(pinned)

    for ref in ("origin/main", "HEAD"):
        try:
            revs.append(git(repo_dir, "rev-parse", ref))
            break
        except RuntimeError:
            continue

    # Keep order, drop duplicates.
    return list(dict.fromkeys(revs))


def git_versions(repo_dir):
    """Versions from every historical version.json, so old pins can be found."""
    versions = set()
    try:
        commits = git(
            repo_dir, "rev-list", "--all", "--", "kernel-cachyos/version.json"
        ).split()
    except RuntimeError:
        return versions

    for commit in commits:
        try:
            data = json.loads(
                git(repo_dir, "show", f"{commit}:kernel-cachyos/version.json")
            )
        except (RuntimeError, json.JSONDecodeError):
            continue
        for value in data.values():
            if isinstance(value, dict) and value.get("version"):
                versions.add(value["version"])
    return versions


def flake_ref(rev, variant):
    return f"{FLAKE}/{rev}#legacyPackages.x86_64-linux.{variant}"


def kernel_outputs(rev, variant):
    ref = flake_ref(rev, variant)
    raw = run(
        [
            "nix",
            "eval",
            "--raw",
            "--apply",
            'k: builtins.concatStringsSep "\\n" (map (o: k.${o}) k.outputs)',
            ref,
        ]
    )
    outputs = [line for line in raw.splitlines() if line]
    if not outputs:
        raise RuntimeError(f"{ref} evaluated to no outputs")
    return ref, outputs


def keep_hashes_for_rev(rev, variant, cache, token):
    ref, outputs = kernel_outputs(rev, variant)

    missing = [
        path
        for path in outputs
        if not narinfo_exists(cache, path.rsplit("/", 1)[-1].split("-", 1)[0])
    ]
    if missing:
        print(
            f"warning: revision {rev[:12]} is not fully cached "
            f"({len(missing)} missing output(s)); not keeping it",
            file=sys.stderr,
        )
        return None, set()

    drv = run(["nix", "eval", "--raw", f"{ref}.drvPath"]).strip()

    # -r includes fixed-output inputs such as the source tarball and config,
    # which are not reachable through the output's runtime references.
    graph = json.loads(run(["nix", "derivation", "show", "-r", drv]))
    paths = set(outputs)
    paths.update(store_paths_in(graph))

    hashes = hashes_from_store_paths(paths)

    # Also protect anything referenced by the cached outputs (e.g. linux-config).
    for path in outputs:
        store_hash = path.rsplit("/", 1)[-1].split("-", 1)[0]
        hashes.update(narinfo_references(narinfo(cache, store_hash)))

    return drv, hashes


def collect_cache_paths(token, cache, repo_dir):
    terms = {
        "-",  # catch-all while the cache has <= 20 paths
        "cachyos",
        "linux-src-patched",
        "kernel-intermediate-config",
        "config",
        *git_versions(repo_dir),
    }

    seen_terms = set()
    results = {}
    truncated_terms = set()

    while terms - seen_terms:
        term = sorted(terms - seen_terms)[0]
        seen_terms.add(term)
        items = search_cache(token, cache, term)
        if len(items) >= SEARCH_LIMIT:
            truncated_terms.add(term)
        for item in items:
            results[item["storeHash"]] = item
            for version in VERSION_RE.findall(item.get("storeSuffix", "")):
                if version not in seen_terms:
                    terms.add(version)

    return results, truncated_terms


def parse_args():
    default_lock = Path.home() / "nixos-config/flake.lock"
    parser = argparse.ArgumentParser(
        prog="purge-cache",
        description="Delete old kernel paths from a Cachix cache.",
    )
    parser.add_argument("--cache", default=os.environ.get("CACHE_NAME", CACHE))
    parser.add_argument("--variant", default=VARIANT)
    parser.add_argument(
        "--keep-rev",
        action="append",
        default=[],
        help="recipe revision whose kernel paths must be kept (repeatable)",
    )
    parser.add_argument(
        "--config-lock",
        default=str(default_lock),
        help="nixos-config flake.lock used to find the currently pinned revision",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="this repository's checkout (used for origin/main and pin history)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="delete every cache path not kept, not just kernel-related ones",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete paths (default is a dry run)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    token = get_token()
    repo_dir = Path(args.repo_dir).resolve()

    keep_revs = args.keep_rev or default_keep_revs(repo_dir, args.config_lock)
    if not keep_revs:
        sys.exit("error: no revision to keep; pass --keep-rev")

    details = cache_details(token, args.cache)
    paths, truncated = collect_cache_paths(token, args.cache, repo_dir)

    print(f"cache:       {args.cache}")
    print(f"used:        {human_size(details['totalFileSize'])}")
    print(f"cache paths: {len(paths)}")

    keep = set()
    for rev in keep_revs:
        print(f"keeping rev: {rev}")
        try:
            drv, hashes = keep_hashes_for_rev(rev, args.variant, args.cache, token)
        except (RuntimeError, urllib.error.URLError) as error:
            sys.exit(f"error: could not evaluate keep revision {rev}: {error}")
        if drv is not None:
            print("  keeping kernel outputs and build inputs")
            keep.update(hashes)

    if not keep:
        sys.exit(
            "error: none of the keep revisions is fully cached; refusing to purge"
        )

    candidates = []
    for store_hash, item in paths.items():
        if store_hash in keep:
            continue
        suffix = item.get("storeSuffix", "")
        if not args.all and not KERNEL_SUFFIX_RE.match(suffix):
            continue
        candidates.append(item)

    candidates.sort(key=lambda item: item.get("lastAccessedTime", ""))
    reclaimable = sum(item.get("fileSize", 0) for item in candidates)

    if truncated:
        print(
            "note: search hit the 20-result limit for "
            + ", ".join(sorted(truncated))
            + "; narrow queries were used for kernel versions",
            file=sys.stderr,
        )

    print()
    if not candidates:
        print("nothing to purge")
        return

    for item in candidates:
        print(
            f"DELETE  {human_size(item.get('fileSize', 0)):>10}  "
            f"{item.get('lastAccessedTime', '')[:10]}  "
            f"{item['storeSuffix']}"
        )
    print(f"\nreclaimable: {human_size(reclaimable)} from {len(candidates)} paths")

    if not args.apply:
        print("dry run; pass --apply to delete these paths")
        return

    deleted = 0
    failed = 0
    for item in candidates:
        store_hash = item["storeHash"]
        try:
            api(
                token,
                f"/cache/{args.cache}/{store_hash}.narinfo",
                method="DELETE",
            )
            deleted += 1
        except urllib.error.HTTPError as error:
            failed += 1
            print(
                f"warning: failed to delete {store_hash}: HTTP {error.code}",
                file=sys.stderr,
            )

    details = cache_details(token, args.cache)
    print(f"\ndeleted {deleted} paths ({failed} failed)")
    print(f"used now: {human_size(details['totalFileSize'])}")


if __name__ == "__main__":
    main()
