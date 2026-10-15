# =============================================================================
# publish_public.py — mirror master into the public showcase repo, safely.
#
# WHY (2026-10-15): the project is shown publicly as a mirror
# (PatrickGolden/Elite-Swing-Trader-Public) while the full history stays in the
# private repo. Copying by hand drifts and invites leaks, so every sync passes
# the same gates, and every gate fails closed:
#
#   1. export the master tree (git archive — tracked files only)
#   2. drop paths matched by scripts/public_exclude.txt
#   3. identifier denylist — regexes in .public_denylist (gitignored, local only,
#      because the identifiers themselves must never be committed). Missing or
#      empty denylist = refuse to sync.
#   4. gitleaks over the exported tree — not installed = refuse to sync.
#   5. mirror into the public checkout and commit locally with a
#      `Source-Commit: <sha>` trailer, then print the diff for review.
#
# Nothing is pushed unless --push is given, and --push re-runs gates 3–4 on the
# public tree first. Never force-pushes.
#
# Run:  python scripts/publish_public.py           (build + commit locally, show diff)
#       python scripts/publish_public.py --check   (only report whether public is behind)
#       python scripts/publish_public.py --push    (push the reviewed sync commit)
# =============================================================================

from __future__ import annotations

import argparse
import fnmatch
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_PUBLIC_DIR = PROJECT.parent / "swing-trader-public"
EXCLUDE_FILE = PROJECT / "scripts" / "public_exclude.txt"
DENYLIST_FILE = PROJECT / ".public_denylist"
SOURCE_REF = "master"
PUBLIC_BRANCH = "main"
SOURCE_TRAILER = "Source-Commit"

# Office files are zips; their text lives in XML members, invisible to a byte scan.
ZIPPED_DOC_SUFFIXES = {".docx", ".xlsx", ".pptx"}
GITLEAKS_WINGET_GLOB = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gitleaks.Gitleaks_*\gitleaks.exe"
)


class GateFailure(Exception):
    """A safety gate refused the sync. The message says which and why."""


# ── helpers ──────────────────────────────────────────────────────────────────

def git(repo: Path, *args: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=capture, text=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise GateFailure(f"git {' '.join(args)} failed in {repo}:\n{result.stderr.strip()}")
    return result.stdout if capture else ""


def load_patterns(path: Path) -> list[str]:
    """Non-blank, non-comment lines."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def is_excluded(rel_path: str, patterns: list[str]) -> bool:
    """
    Gitignore-like subset, kept deliberately small:
      'dir/'      -> everything under that directory (from the repo root)
      'a/b*.md'   -> fnmatch against the full path when the pattern has a '/'
      'audit_*.md'-> fnmatch against the file name at any depth otherwise
    """
    name = rel_path.rsplit("/", 1)[-1]
    for pat in patterns:
        if pat.endswith("/"):
            if rel_path.startswith(pat):
                return True
        elif "/" in pat:
            if fnmatch.fnmatchcase(rel_path, pat):
                return True
        elif fnmatch.fnmatchcase(name, pat):
            return True
    return False


def compile_denylist(patterns: list[str]) -> list[re.Pattern]:
    if not patterns:
        raise GateFailure(
            "Denylist (.public_denylist) is missing or empty — refusing to sync. "
            "It holds the personal identifiers (wallet address, email, paths) that must never go public."
        )
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def readable_text(path: Path) -> str:
    """File text for scanning; Office documents are unzipped so their XML is searched too."""
    data = path.read_bytes()
    if path.suffix.lower() in ZIPPED_DOC_SUFFIXES:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                data = b"\n".join(zf.read(m) for m in zf.namelist() if m.endswith(".xml"))
        except zipfile.BadZipFile:
            pass
    return data.decode("utf-8", errors="ignore")


def denylist_hits(root: Path, deny: list[re.Pattern], files: list[str]) -> list[str]:
    hits = []
    for rel in files:
        text = readable_text(root / rel)
        for rx in deny:
            if rx.search(text):
                hits.append(f"{rel}  (matches denylist pattern #{deny.index(rx) + 1})")
    return hits


def find_gitleaks() -> str:
    found = shutil.which("gitleaks") or next(iter(glob.glob(GITLEAKS_WINGET_GLOB)), None)
    if not found:
        raise GateFailure("gitleaks not found — refusing to sync (winget install Gitleaks.Gitleaks).")
    return found


def run_gitleaks(target: Path) -> None:
    result = subprocess.run(
        [find_gitleaks(), "dir", str(target), "--redact", "--no-banner", "--exit-code", "1"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise GateFailure(f"gitleaks found leaks in {target}:\n{result.stdout}{result.stderr}")


def last_synced_source(public_dir: Path) -> str | None:
    out = git(public_dir, "log", PUBLIC_BRANCH,
              f"--format=%(trailers:key={SOURCE_TRAILER},valueonly)")
    for line in out.splitlines():
        if line.strip():
            return line.strip()
    return None


# ── gates ────────────────────────────────────────────────────────────────────

def export_tree(source_ref: str, dest: Path) -> list[str]:
    """Write the tracked files of source_ref into dest; return their repo-relative paths."""
    archive = subprocess.run(
        ["git", "-C", str(PROJECT), "archive", "--format=tar", source_ref],
        capture_output=True, check=False,
    )
    if archive.returncode != 0:
        raise GateFailure(f"git archive {source_ref} failed:\n{archive.stderr.decode(errors='ignore')}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        tar.extractall(dest, members=members, filter="data")
    return sorted(m.name for m in members)


def check_public_checkout(public_dir: Path) -> None:
    if not (public_dir / ".git").exists():
        raise GateFailure(f"{public_dir} is not a git checkout.")
    branch = git(public_dir, "branch", "--show-current").strip()
    if branch != PUBLIC_BRANCH:
        raise GateFailure(f"{public_dir} is on '{branch}', expected '{PUBLIC_BRANCH}'.")
    if git(public_dir, "status", "--porcelain").strip():
        raise GateFailure(f"{public_dir} has uncommitted changes — commit or discard them first.")
    git(public_dir, "fetch", "-q", "origin")
    behind = int(git(public_dir, "rev-list", "--count", f"{PUBLIC_BRANCH}..origin/{PUBLIC_BRANCH}"))
    if behind:
        raise GateFailure(f"{public_dir} is {behind} commit(s) behind origin — pull first.")


def mirror(export_dir: Path, files: list[str], public_dir: Path) -> None:
    """Make the public working tree's tracked files exactly `files`."""
    keep = set(files)
    for rel in git(public_dir, "ls-files").splitlines():
        if rel not in keep:
            (public_dir / rel).unlink(missing_ok=True)
    for rel in files:
        target = public_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(export_dir / rel, target)
    git(public_dir, "add", "-u")
    # -f: the export legitimately contains force-tracked paths (e.g. data/ research files)
    for start in range(0, len(files), 200):
        git(public_dir, "add", "-f", "--", *files[start:start + 200])


def sync_message(source_sha: str, previous: str | None) -> str:
    short = source_sha[:7]
    body = ""
    if previous:
        subjects = git(PROJECT, "log", "--format=- %s", f"{previous}..{source_sha}").strip()
        body = f"\n\nPrivate commits included:\n{subjects}" if subjects else ""
    return f"Sync with private master @{short}{body}\n\n{SOURCE_TRAILER}: {source_sha}\n"


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_check(public_dir: Path) -> int:
    source_sha = git(PROJECT, "rev-parse", SOURCE_REF).strip()
    previous = last_synced_source(public_dir)
    if previous is None:
        print(f"Public mirror has no {SOURCE_TRAILER} trailer yet — run a first sync.")
        return 1
    if previous == source_sha:
        print(f"Public mirror is in sync with {SOURCE_REF} @{source_sha[:7]}.")
        return 0
    behind = git(PROJECT, "rev-list", "--count", f"{previous}..{source_sha}").strip()
    print(f"Public mirror is {behind} commit(s) behind {SOURCE_REF} "
          f"(last synced @{previous[:7]}). Run: python scripts/publish_public.py")
    return 1


def cmd_sync(public_dir: Path, denylist: Path) -> int:
    check_public_checkout(public_dir)
    deny = compile_denylist(load_patterns(denylist))
    excludes = load_patterns(EXCLUDE_FILE)
    source_sha = git(PROJECT, "rev-parse", SOURCE_REF).strip()

    with tempfile.TemporaryDirectory(prefix="public-export-") as tmp:
        export_dir = Path(tmp)
        all_files = export_tree(source_sha, export_dir)
        files = [f for f in all_files if not is_excluded(f, excludes)]
        for f in set(all_files) - set(files):
            (export_dir / f).unlink()
        print(f"Exported {len(all_files)} files from {SOURCE_REF} @{source_sha[:7]}; "
              f"{len(all_files) - len(files)} excluded, {len(files)} to publish.")

        hits = denylist_hits(export_dir, deny, files)
        if hits:
            raise GateFailure("Denylisted identifiers found — fix them on master first:\n  "
                              + "\n  ".join(hits))
        run_gitleaks(export_dir)
        print("Gates passed: denylist clean, gitleaks clean.")

        message = sync_message(source_sha, last_synced_source(public_dir))
        if any(rx.search(message) for rx in deny):
            raise GateFailure("A private commit subject matches the denylist — not publishing its message.")

        mirror(export_dir, files, public_dir)

    if not git(public_dir, "status", "--porcelain").strip():
        print("Public mirror already matches — nothing to commit.")
        return 0
    commit = subprocess.run(
        ["git", "-C", str(public_dir), "commit", "-q", "-F", "-"],
        input=message, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if commit.returncode != 0:
        raise GateFailure(f"git commit failed in {public_dir}:\n{commit.stderr.strip()}")
    print("\nCommitted locally (NOT pushed). Review:\n")
    print(git(public_dir, "show", "--stat", "--format=%h %s%n", "HEAD"))
    print("When approved:  python scripts/publish_public.py --push")
    return 0


def cmd_push(public_dir: Path, denylist: Path) -> int:
    deny = compile_denylist(load_patterns(denylist))
    git(public_dir, "fetch", "-q", "origin")
    ahead = int(git(public_dir, "rev-list", "--count", f"origin/{PUBLIC_BRANCH}..{PUBLIC_BRANCH}"))
    if not ahead:
        print("Nothing to push.")
        return 0
    if git(public_dir, "status", "--porcelain").strip():
        raise GateFailure(f"{public_dir} has uncommitted changes — refusing to push.")
    tracked = git(public_dir, "ls-files").splitlines()
    hits = denylist_hits(public_dir, deny, tracked)
    if hits:
        raise GateFailure("Denylisted identifiers in the public tree:\n  " + "\n  ".join(hits))
    run_gitleaks(public_dir)
    git(public_dir, "push", "origin", PUBLIC_BRANCH, capture=False)
    print(f"Pushed {ahead} commit(s) to the public mirror.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror master into the public showcase repo")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only report whether public is behind")
    mode.add_argument("--push", action="store_true", help="push the reviewed local sync commit")
    ap.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR)
    ap.add_argument("--denylist", type=Path, default=DENYLIST_FILE)
    args = ap.parse_args()
    try:
        if args.check:
            return cmd_check(args.public_dir)
        if args.push:
            return cmd_push(args.public_dir, args.denylist)
        return cmd_sync(args.public_dir, args.denylist)
    except GateFailure as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
