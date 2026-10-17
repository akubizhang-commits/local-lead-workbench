#!/usr/bin/env python3
"""Apply/check the pinned local Maps patch without checkout, reset or network."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PINNED_COMMIT = "549e4b5e61c7103685ef8392f246ebdba783ed03"
ROOT = Path(__file__).resolve().parents[1]
PATCH = Path(__file__).with_name("maps_stability.patch")
# scrapemate is a module dependency, not part of the checkout. A patched copy is
# generated inside the checkout and used through a go.mod replace directive.
SCRAPEMATE_MODULE = "github.com/gosom/scrapemate"
SCRAPEMATE_VERSION = "v1.4.0"
SCRAPEMATE_FILE = "adapters/fetchers/jshttp/jshttp.go"
SCRAPEMATE_SUM = "h1:gW5frtHtmQHUG/oGGpQM6y2azkOK8EgIBFxl8oAOvIA="
SCRAPEMATE_FILE_SHA256 = "13c2b7d0db3922be771f512f0c17ac8a432c8fbadc8c159dba8d7b84159fa53b"
SCRAPEMATE_PATCH = Path(__file__).with_name("maps_scrapemate_launch.patch")
SCRAPEMATE_COPY = Path("local_overlay") / "scrapemate"
EXPECTED_INFO = {
    "patch": "local-maps-stability-v2",
    "search_max_retries": 0,
    "place_max_retries": 0,
    "fast_search_max_retries": 0,
    "detail_reload": False,
    "navigation_gap_seconds": 5,
    "chromium_single_process": False,
}


def _git(source, *args):
    return subprocess.run(["git", "-C", str(source), *args],
                          capture_output=True, text=True, check=False)


def _check_revision(source):
    top = _git(source, "rev-parse", "--show-toplevel")
    revision = _git(source, "rev-parse", "HEAD")
    if (top.returncode or revision.returncode
            or Path(top.stdout.strip()).resolve() != Path(source).resolve()
            or revision.stdout.strip() != PINNED_COMMIT):
        raise ValueError(f"Maps source revision mismatch: require local checkout {PINNED_COMMIT}")


def verify_patch(source):
    _check_revision(source)
    result = _git(source, "apply", "--reverse", "--check", str(PATCH))
    if result.returncode:
        raise ValueError("Maps stability patch mismatch/conflict; existing files preserved:\n"
                         + result.stderr.strip())


def apply_patch(source):
    _check_revision(source)
    if _git(source, "apply", "--reverse", "--check", str(PATCH)).returncode == 0:
        return
    check = _git(source, "apply", "--check", str(PATCH))
    if check.returncode:
        raise ValueError("Maps stability patch mismatch/conflict; existing files preserved:\n"
                         + check.stderr.strip())
    # git apply checks every hunk before writing; no --reject/checkout/reset.
    result = _git(source, "apply", str(PATCH))
    if result.returncode:
        raise ValueError("Maps stability patch conflict:\n" + result.stderr.strip())
    verify_patch(source)


def _tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + _sha(path).encode() + b"\n")
    return digest.hexdigest()


def prepare_scrapemate(source, go="go"):
    """Copy pinned scrapemate from the Go module cache into the checkout and patch it.

    maps_stability.patch points go.mod at this copy with a replace directive, so a
    build without this step fails instead of shipping upstream's Chromium flags.
    """
    verify_patch(source)
    # Uses the caller's GOPROXY: bootstrap may download, --offline (GOPROXY=off) may not.
    found = subprocess.run([str(go), "mod", "download", "-json",
                            f"{SCRAPEMATE_MODULE}@{SCRAPEMATE_VERSION}"],
                           cwd=source, capture_output=True, text=True, check=False)
    try:
        info = json.loads(found.stdout)
    except ValueError:
        info = {}
    module_dir = info.get("Dir")
    if found.returncode or info.get("Sum") != SCRAPEMATE_SUM or not module_dir:
        raise ValueError(f"scrapemate {SCRAPEMATE_VERSION} unavailable or checksum mismatch:\n"
                         + (found.stderr.strip() or str(info.get("Sum"))))
    if _sha(Path(module_dir) / SCRAPEMATE_FILE) != SCRAPEMATE_FILE_SHA256:
        raise ValueError(f"scrapemate {SCRAPEMATE_FILE} mismatch; copy not written")
    target = Path(source) / SCRAPEMATE_COPY
    with tempfile.TemporaryDirectory() as work:
        staged = Path(work) / "scrapemate"
        shutil.copytree(module_dir, staged)
        for path in [staged, *staged.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        # Never let git discover an enclosing repository; it then behaves like patch(1).
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(work).resolve())}
        result = subprocess.run(["git", "apply", str(SCRAPEMATE_PATCH.resolve())], cwd=staged,
                                capture_output=True, text=True, check=False, env=env)
        if result.returncode:
            raise ValueError("scrapemate launch patch conflict:\n" + result.stderr.strip())
        if target.is_dir() and _tree_digest(target) == _tree_digest(staged):
            return
        target.parent.mkdir(exist_ok=True)
        previous = target.with_name(target.name + ".old")
        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.rename(previous)
        shutil.copytree(staged, target)
        if previous.exists():
            shutil.rmtree(previous)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(source):
    """Include tracked and untracked build inputs, including prior local patches."""
    listing = _git(source, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listing.returncode:
        raise ValueError("Cannot fingerprint Maps source")
    digest = hashlib.sha256()
    for name in sorted(set(listing.stdout.split("\0")) - {""}):
        path = Path(source) / name
        if path.is_symlink():
            value = "symlink:" + os.readlink(path)
        elif path.is_file():
            value = _sha(path)
        else:
            value = "deleted"
        digest.update(name.encode() + b"\0" + value.encode() + b"\n")
    return digest.hexdigest()


def write_manifest(source, candidate, output, expected_source=None):
    verify_patch(source)
    digest = source_digest(source)
    if expected_source is not None and digest != expected_source:
        raise ValueError("Maps source changed during build; candidate not published")
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env["DISABLE_TELEMETRY"] = "1"
    result = subprocess.run([str(Path(candidate).resolve()), "-local-stability-info"],
                            capture_output=True, text=True, env=env, timeout=15)
    try:
        info = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("Maps compiled policy check did not return JSON") from exc
    if result.returncode or info != EXPECTED_INFO:
        raise ValueError(f"Maps compiled policy mismatch: {info!r}; candidate not published")
    manifest = {
        "repository": "https://github.com/gosom/google-maps-scraper",
        "commit": PINNED_COMMIT,
        "stability": info,
        "patch_sha256": _sha(PATCH),
        "scrapemate_patch_sha256": _sha(SCRAPEMATE_PATCH),
        "scrapemate_tree_sha256": _tree_digest(Path(source) / SCRAPEMATE_COPY),
        "source_sha256": digest,
        "binary_sha256": _sha(candidate),
        "build": {"offline": True, "flags": ["-mod=readonly", "-trimpath", "-buildvcs=false"]},
    }
    with Path(output).open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?",
                        type=Path, default=ROOT / "vendor" / f"google-maps-scraper-{PINNED_COMMIT}")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--source-digest", action="store_true")
    modes.add_argument("--manifest", nargs=2, metavar=("CANDIDATE", "OUTPUT"), type=Path)
    modes.add_argument("--prepare-scrapemate", action="store_true")
    parser.add_argument("--go", default=os.environ.get("GO", "go"))
    parser.add_argument("--expected-source")
    args = parser.parse_args()
    try:
        if args.prepare_scrapemate:
            prepare_scrapemate(args.source, args.go)
            print("Patched scrapemate copy prepared")
        elif args.manifest:
            write_manifest(args.source, *args.manifest, expected_source=args.expected_source)
        elif args.source_digest:
            verify_patch(args.source)
            print(source_digest(args.source))
        elif args.check:
            verify_patch(args.source)
            print("Maps stability patch verified")
        else:
            apply_patch(args.source)
            print("Maps stability patch applied/verified; existing unrelated changes preserved")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
