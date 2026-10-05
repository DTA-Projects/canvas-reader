"""Content conversion: Canvas HTML → Markdown, and PDF → extractable text."""

from __future__ import annotations

import re
from pathlib import Path

from markdownify import markdownify as _markdownify
from pypdf import PdfReader


def html_to_markdown(html: str) -> str:
    """Turn a Canvas HTML fragment into clean Markdown with LF endings."""
    if not html or not html.strip():
        return ""
    text = _markdownify(html, heading_style="ATX", bullets="-")
    text = text.replace("\xa0", " ")  # &nbsp;
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def rewrite_links(
    text: str,
    *,
    host: str,
    page_targets: dict[str, str],
    file_targets: dict[int, str],
) -> str:
    """Point Canvas links at the local files that were just written.

    page_targets: "/courses/1/pages/week-1" -> "week-1.md"  (caller sets the dir prefix)
    file_targets: 42 -> "../files/notes.pdf"                (caller sets the dir prefix)

    Links that don't match (assignments, external tools, ...) stay absolute so
    they still open Canvas in a browser.
    """
    for path, local in page_targets.items():
        for prefix in (host, ""):
            pattern = re.compile(re.escape(prefix + path) + r"(?![A-Za-z0-9._-])")
            text = pattern.sub(lambda _m, loc=local: loc, text)

    for file_id, local in file_targets.items():
        pattern = re.compile(
            rf"(?:{re.escape(host)})?(?:/courses/\d+)?/files/{file_id}(?![0-9])"
            rf"(?:/download)?[^\s)\]\"'#]*"
        )
        text = pattern.sub(lambda _m, loc=local: loc, text)

    return text


def extract_pdf_text(pdf: Path) -> Path | None:
    """Write a .txt sidecar next to a PDF so grep/full-text search can see it.

    Returns None (and leaves sync running) if extraction fails — the PDF itself
    is still readable with the agent's `read` tool.
    """
    try:
        reader = PdfReader(pdf)
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n\n".join(part for part in parts if part)
        sidecar = pdf.with_suffix(".txt")
        sidecar.write_text(text, encoding="utf-8", newline="\n")
        return sidecar
    except Exception:  # corrupt or exotic PDFs must not kill a sync
        return None
