"""Shared fixtures — every test runs offline against mocked HTTP."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from canvas_reader.canvas import Canvas
from canvas_reader.store import Config

HOST = "https://school.instructure.com"


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def study_remote(tmp_path) -> Path:
    """A local bare git repo standing in for the private study-materials repo.

    Seeded with a handwritten note so tests can prove tool publishing never
    touches anything outside the managed canvas/ subtree. Fully offline.
    """
    bare = tmp_path / "study-remote.git"
    _git("init", "-q", "-b", "main", "--bare", str(bare))
    seed = tmp_path / "study-seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", str(seed))
    _git("remote", "add", "origin", str(bare), cwd=seed)
    note = seed / "Calculus" / "My Notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Hand-written notes\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "seed",
        cwd=seed,
    )
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    return bare


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        host=HOST,
        token=None,
        cookie="_canvas_session=test-cookie",
        content_dir=tmp_path / "content",
    )


@pytest.fixture
def token_cfg(tmp_path) -> Config:
    return Config(
        host=HOST,
        token="1234~abc",
        cookie=None,
        content_dir=tmp_path / "content",
    )


class SleepRecorder:
    """Stands in for time.sleep so tests can assert on recorded delays."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def tiny_pdf() -> bytes:
    """A real, minimal PDF (starts with %PDF- and parses cleanly)."""
    from io import BytesIO

    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def client(cfg) -> Canvas:
    """A cookie-mode client whose sleeps are recorded instead of performed."""
    c = Canvas(cfg)
    recorder = SleepRecorder()
    c._sleep = recorder  # noqa: SLF001 — tests assert on recorded delays
    c.sleeps = recorder.calls
    return c
