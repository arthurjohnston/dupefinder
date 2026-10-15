"""Publish the General Index case pages: commit example_output/general_index/ to main and push, so GitHub Pages
(.github/workflows/pages.yml) puts them at gi_report_case.SITE_URL.

The working branch usually has unrelated uncommitted work, so this never touches it: it fetches origin, adds a
detached worktree of origin/main in a temporary directory, copies example_output/general_index/ over that
worktree's copy, commits only that folder, pushes HEAD to main, and removes the worktree. Nothing under
data/ is ever copied (the site folder holds only the comparison pages, the index and cases.json).

gi_review_cli.py runs it after writing a case's report files, once you've confirmed a problem verdict.

Usage: gi_publish_site.py [GI_NUMBER ...]   (the numbers only go into the commit message)
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = Path("example_output") / "general_index"


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def publish(cases=(), remote="origin", branch="main", root=ROOT):
    """Commit and push the site folder to remote/branch. Returns the new commit's short hash, or None when main
    already has exactly these files. Raises subprocess.CalledProcessError (with git's message) on failure."""
    root = Path(root)
    git("fetch", "-q", remote, branch, cwd=root)
    tmp = Path(tempfile.mkdtemp(prefix="gi-site-"))
    wt = tmp / "main"
    git("worktree", "add", "-q", "--detach", str(wt), f"{remote}/{branch}", cwd=root)
    try:
        dest = wt / SITE
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root / SITE, dest)
        git("add", "-A", "--", str(SITE), cwd=wt)
        if not git("diff", "--cached", "--quiet", check=False, cwd=wt).returncode:
            return None
        names = ", ".join(cases) if cases else "update"
        msg = (f"General Index site: report {names}\n\n"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n")
        git("commit", "-q", "-m", msg, cwd=wt)
        git("push", "-q", remote, f"HEAD:{branch}", cwd=wt)
        return git("rev-parse", "--short", "HEAD", cwd=wt).stdout.strip()
    finally:
        git("worktree", "remove", "--force", str(wt), cwd=root, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gi", nargs="*", type=int)
    args = ap.parse_args()
    try:
        sha = publish([f"GI-{n:02d}" for n in args.gi])
    except subprocess.CalledProcessError as e:
        sys.exit(f"publish failed: {' '.join(e.cmd)}\n{e.stderr.strip()}")
    print(f"pushed {sha} to main" if sha else "main already has these pages: nothing to push")


if __name__ == "__main__":
    main()
