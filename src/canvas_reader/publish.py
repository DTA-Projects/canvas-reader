"""Publish synced Markdown to the user's private study repo.

Only the tool-managed ``canvas/`` subtree of the clone is ever written or
replaced; handwritten notes in the rest of the repo are never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .store import Config

SUBTREE = "canvas"  # the only folder this tool writes in the study repo
CLONE_NAME = ".study-materials"  # local mirror, living inside content/ (gitignored)

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # fail fast, never hang on a prompt


class PublishError(Exception):
    """A git operation failed. Publishing is best-effort and never fatal to sync."""


def _git(clone: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise PublishError(f"git {' '.join(args)}: {detail}")
    return proc


def _ensure_clone(url: str, clone: Path) -> Path:
    """Clone the study repo once, then fast-forward it before each publish.

    A pull that cannot fast-forward (changed STUDY_REPO, diverged mirror) simply
    discards the local mirror and re-clones — content/ and the remote are the
    only sources of truth, so nothing can be lost that way.
    """
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--quiet", url, str(clone)],
            capture_output=True,
            text=True,
            env=_GIT_ENV,
        )
        if proc.returncode != 0:
            raise PublishError(f"clone {url}: {(proc.stderr or proc.stdout).strip()}")
        return clone
    _git(clone, "remote", "set-url", "origin", url)  # STUDY_REPO may have changed
    try:
        _git(clone, "pull", "--ff-only", "--quiet")
    except PublishError:
        shutil.rmtree(clone, ignore_errors=True)
        return _ensure_clone(url, clone)
    return clone


def _identity_args(clone: Path) -> list[str]:
    """Commit as the user's git identity when one is configured, else a generic author."""
    if _git(clone, "config", "--get", "user.email", check=False).stdout.strip():
        return []
    return [
        "-c",
        "user.name=canvas-reader",
        "-c",
        "user.email=canvas-reader@users.noreply.github.com",
    ]


def publish_markdown(cfg: Config) -> str | None:
    """Copy every course's Markdown into the managed subtree, commit, push.

    Returns a short status line, or None when STUDY_REPO is not configured.
    """
    if not cfg.study_repo:
        return None
    source = cfg.content_dir / "courses"
    if not source.is_dir():
        raise PublishError(f"nothing to publish: {source} does not exist")

    clone = _ensure_clone(cfg.study_repo, cfg.content_dir / CLONE_NAME)
    managed = clone / SUBTREE
    shutil.rmtree(managed, ignore_errors=True)

    count = 0
    for course_dir in sorted(source.iterdir()):
        if not course_dir.is_dir():
            continue
        target = managed / course_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for md in sorted(course_dir.glob("*.md")):
            shutil.copy2(md, target / md.name)
        pages = course_dir / "pages"
        if pages.is_dir():
            shutil.copytree(pages, target / "pages")
        count += 1
    if not count:
        raise PublishError(f"no course folders in {source}")

    _git(clone, "add", "-A", "--", SUBTREE)
    if _git(clone, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return f"already up to date ({count} classes)"
    message = f"canvas sync · {count} classes · {date.today().isoformat()}"
    _git(clone, *_identity_args(clone), "commit", "--quiet", "-m", message)
    try:
        _git(clone, "push", "--quiet")
    except PublishError as exc:
        raise PublishError(
            f"{exc} (hint: pushing needs git credentials for the host — "
            "`gh auth setup-git` configures the GitHub CLI as a helper)"
        ) from exc
    return f"pushed {count} classes"
