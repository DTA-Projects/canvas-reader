"""Local concerns: configuration, safe filenames, atomic writes, sync manifest.

Nothing in this module talks to the network.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

MANIFEST_NAME = "manifest.json"

PLACEHOLDER_MARKERS = ("PASTE", "CHANGEME", "YOUR_", "<")

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class ConfigError(Exception):
    """Local configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    host: str  # e.g. https://school.instructure.com
    token: str | None  # API token (Option A)
    cookie: str | None  # full Cookie header value (Option B)
    content_dir: Path
    textbooks_dir: Path | None = None  # optional shared folder of course textbooks

    @property
    def auth_mode(self) -> str:
        return "token" if self.token else "session"


def _is_placeholder(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


def cookie_header(value: str) -> str:
    """Accept a bare `_canvas_session` value or a full Cookie header."""
    value = value.strip().strip('"').replace("\r", "").replace("\n", "")
    if value.startswith("_canvas_session=") or ";" in value:
        return value
    return f"_canvas_session={value}"


def load_config() -> Config:
    """Read credentials from the process environment, then from .env.

    find_dotenv(usecwd=True) walks up from the working directory — plain
    load_dotenv() walks from site-packages, which never finds your project.
    """
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file)

    host = os.environ.get("CANVAS_HOST", "").strip()
    token = os.environ.get("CANVAS_API_TOKEN", "").strip()
    session = os.environ.get("CANVAS_SESSION", "").strip()
    content = os.environ.get("CONTENT_DIR", "").strip() or "content"
    textbooks = os.environ.get("TEXTBOOKS_DIR", "").strip()

    problems = []
    if not host:
        problems.append("CANVAS_HOST is not set (run `canvas init`)")
    for name, value in (("CANVAS_API_TOKEN", token), ("CANVAS_SESSION", session)):
        if value and _is_placeholder(value):
            problems.append(f"{name} still holds a placeholder — fill it in or delete the line")
    if not token and not session:
        problems.append(
            "no credentials: set CANVAS_API_TOKEN or CANVAS_SESSION (README → Authentication)"
        )
    if problems:
        raise ConfigError("; ".join(problems))

    if not host.startswith(("http://", "https://")):
        host = "https://" + host
    host = host.rstrip("/")

    return Config(
        host=host,
        token=token or None,
        cookie=cookie_header(session) if session else None,
        content_dir=Path(content).expanduser(),
        textbooks_dir=Path(textbooks).expanduser() if textbooks else None,
    )


def slugify(text: str, max_len: int = 80) -> str:
    """A filename-safe slug that behaves identically on Linux, macOS, and Windows."""
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    if not text:
        text = "untitled"
    if text in _WINDOWS_RESERVED:
        text = f"{text}-file"
    return text[:max_len].strip("-") or "untitled"


def safe_filename(name: str, fallback: str = "file") -> str:
    """Sanitize a server-supplied filename, keeping its extension."""
    name = Path(name.replace("\\", "/")).name  # drop any path components
    if name.startswith(".") and name.count(".") == 1:
        stem, ext = "", name  # extension-only name like ".pdf"
    else:
        stem, ext = os.path.splitext(name)
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext).lower()[:10]
    base = slugify(stem) if stem.strip() else slugify(fallback)
    return base + ext


@contextmanager
def atomic_output(path: Path) -> Iterator[Path]:
    """Yield a temp file beside `path`, then rename it into place.

    os.replace() is atomic on all three platforms and dodges Windows file locks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings, atomically."""
    with atomic_output(path) as tmp:
        tmp.write_text(text, encoding="utf-8", newline="\n")


def load_manifest(content_dir: Path) -> dict:
    """Read the sync manifest; a corrupt one just means a full re-sync."""
    path = content_dir / MANIFEST_NAME
    if not path.exists():
        return {"courses": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"courses": {}}
    if not isinstance(data, dict) or not isinstance(data.get("courses"), dict):
        return {"courses": {}}
    return data


def save_manifest(content_dir: Path, manifest: dict) -> None:
    write_text(content_dir / MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def write_env_file(path: Path, host: str, token: str | None, session: str | None) -> None:
    """Write a .env with quoted values and owner-only permissions."""

    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [f"CANVAS_HOST={quote(host)}"]
    if token:
        lines.append(f"CANVAS_API_TOKEN={quote(token)}")
    if session:
        lines.append(f"CANVAS_SESSION={quote(session)}")
    lines += [
        "",
        "# Optional: where synced content is written (default: ./content)",
        "# CONTENT_DIR=content",
        "",
        "# Optional: shared folder of course textbooks (Windows: Z:\\Textbooks,",
        "# WSL: /mnt/z/Textbooks) — agents read PDFs from here",
        "# TEXTBOOKS_DIR=",
        "",
    ]
    write_text(path, "\n".join(lines))
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows has its own permission model
        pass
