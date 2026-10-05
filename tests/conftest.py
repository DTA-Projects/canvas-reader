"""Shared fixtures — every test runs offline against mocked HTTP."""

from __future__ import annotations

import pytest

from canvas_reader.canvas import Canvas
from canvas_reader.store import Config

HOST = "https://school.instructure.com"


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
