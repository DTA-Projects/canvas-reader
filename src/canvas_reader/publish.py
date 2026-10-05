"""Publish synced Markdown to the user's private study repo.

The clone's ``canvas/`` subtree mixes tool-published files with the user's own
notes. The tool only ever replaces or removes files it published itself
(recorded in ``.study-materials.json``); anything the user adds in the clone
survives every publish.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .store import Config, write_text

SUBTREE = "canvas"  # the folder this tool publishes into
CLONE_NAME = ".study-materials"  # local mirror, living inside content/ (gitignored)
RECORD_NAME = ".study-materials.json"  # files the tool published last time

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

    A pull that cannot fast-forward re-clones — but only when the mirror has no
    local changes, so uncommitted work (e.g. handwritten notes) is never
    discarded. content/ and the remote are the sources of truth.
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
    except PublishError as exc:
        if _git(clone, "status", "--porcelain").stdout.strip():
            raise PublishError(f"mirror cannot fast-forward and has local changes: {exc}") from exc
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


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty after tool files were deleted."""
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            path.rmdir()  # only succeeds when empty
        except OSError:
            pass


def publish_markdown(cfg: Config) -> str | None:
    """Copy every course's Markdown into the managed subtree, commit, push.

    Files the tool published before but that no longer exist locally are
    removed; anything else under the subtree is left alone. Returns a short
    status line, or None when STUDY_REPO is not configured.
    """
    if not cfg.study_repo:
        return None
    source = cfg.content_dir / "courses"
    if not source.is_dir():
        raise PublishError(f"nothing to publish: {source} does not exist")

    clone = _ensure_clone(cfg.study_repo, cfg.content_dir / CLONE_NAME)
    try:
        previous = set(json.loads((cfg.content_dir / RECORD_NAME).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        previous = set()

    managed = clone / SUBTREE
    published: set[str] = set()
    count = 0
    for course_dir in sorted(source.iterdir()):
        if not course_dir.is_dir():
            continue
        count += 1
        rel_course = f"{SUBTREE}/{course_dir.name}"
        candidates = sorted(course_dir.glob("*.md"))
        pages = course_dir / "pages"
        if pages.is_dir():
            candidates += sorted(pages.rglob("*.md"))
        for md in candidates:
            rel = f"{rel_course}/{md.relative_to(course_dir).as_posix()}"
            dest = clone / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(md, dest)
            published.add(rel)
    if not count:
        raise PublishError(f"no course folders in {source}")

    # Tool files that vanished from content/ are removed; user files never were
    # in the record, so they always survive.
    for rel in sorted(previous - published):
        path = clone / rel
        if path.is_file():
            path.unlink()
    _prune_empty_dirs(managed)
    write_text(
        cfg.content_dir / RECORD_NAME,
        json.dumps(sorted(published), indent=1) + "\n",
    )

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
