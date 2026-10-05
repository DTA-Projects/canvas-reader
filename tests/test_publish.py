"""Publishing: Markdown mirror → private study repo (local bare repo stand-in).

Everything runs offline — the "GitHub" remote is a bare repo in tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import responses

from canvas_reader.cli import main
from canvas_reader.publish import PublishError, publish_markdown
from canvas_reader.store import Config

HOST = "https://school.instructure.com"


def _cfg(content_dir: Path, study_repo: Path | None = None) -> Config:
    return Config(
        host=HOST,
        token=None,
        cookie="_canvas_session=test-cookie",
        content_dir=content_dir,
        study_repo=str(study_repo) if study_repo else None,
    )


def _make_content(content: Path) -> None:
    course = content / "courses" / "101-sample-course"
    (course / "pages").mkdir(parents=True)
    (course / "README.md").write_text("# Sample Course\n", encoding="utf-8")
    (course / "modules.md").write_text("# Modules\n", encoding="utf-8")
    (course / "pages" / "week-1.md").write_text("## Week 1\n", encoding="utf-8")
    (course / "files").mkdir()
    (course / "files" / "book.pdf").write_bytes(b"%PDF-1.4 fake textbook")


def _remote_files(bare: Path) -> set[str]:
    out = subprocess.run(
        ["git", f"--git-dir={bare}", "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.splitlines())


def _remote_commits(bare: Path) -> int:
    out = subprocess.run(
        ["git", f"--git-dir={bare}", "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


class TestPublish:
    def test_disabled_without_study_repo(self, tmp_path):
        assert publish_markdown(_cfg(tmp_path / "content")) is None

    def test_pushes_markdown_only_and_preserves_notes(self, tmp_path, study_remote):
        _make_content(tmp_path / "content")
        status = publish_markdown(_cfg(tmp_path / "content", study_remote))
        assert status == "pushed 1 classes"
        files = _remote_files(study_remote)
        assert files == {
            "Calculus/My Notes.md",  # handwritten note, untouched
            "canvas/101-sample-course/README.md",
            "canvas/101-sample-course/modules.md",
            "canvas/101-sample-course/pages/week-1.md",
        }
        assert not any(name.endswith(".pdf") for name in files)  # PDFs never pushed

    def test_second_publish_is_a_noop(self, tmp_path, study_remote):
        _make_content(tmp_path / "content")
        cfg = _cfg(tmp_path / "content", study_remote)
        publish_markdown(cfg)
        commits = _remote_commits(study_remote)
        status = publish_markdown(cfg)
        assert status == "already up to date (1 classes)"
        assert _remote_commits(study_remote) == commits  # no empty commits

    def test_locally_deleted_page_disappears(self, tmp_path, study_remote):
        _make_content(tmp_path / "content")
        cfg = _cfg(tmp_path / "content", study_remote)
        publish_markdown(cfg)
        (tmp_path / "content" / "courses" / "101-sample-course" / "pages" / "week-1.md").unlink()
        publish_markdown(cfg)
        files = _remote_files(study_remote)
        assert "canvas/101-sample-course/pages/week-1.md" not in files
        assert "canvas/101-sample-course/README.md" in files
        assert "Calculus/My Notes.md" in files

    def test_missing_content_raises_publish_error(self, tmp_path, study_remote):
        with pytest.raises(PublishError, match="nothing to publish"):
            publish_markdown(_cfg(tmp_path / "content", study_remote))

    def test_unreachable_remote_raises_publish_error(self, tmp_path):
        _make_content(tmp_path / "content")
        bad = tmp_path / "no-such-remote.git"
        with pytest.raises(PublishError, match="clone"):
            publish_markdown(_cfg(tmp_path / "content", bad))


class TestSyncAutoPublish:
    @responses.activate
    def test_sync_pushes_afterwards(self, monkeypatch, tmp_path, study_remote, tiny_pdf, capsys):
        from test_cli import _mock_canvas  # shared end-to-end Canvas mock

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CANVAS_HOST", HOST)
        monkeypatch.setenv("CANVAS_SESSION", "session-cookie-value")
        monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
        monkeypatch.setenv("CONTENT_DIR", str(tmp_path / "content"))
        monkeypatch.setenv("STUDY_REPO", str(study_remote))
        _mock_canvas(responses, tiny_pdf=tiny_pdf)

        assert main(["sync", "--json"]) == 0
        captured = capsys.readouterr()
        assert "↑ study-materials: pushed 1 classes" in captured.err
        assert "canvas/101-sample-course/pages/week-1.md" in _remote_files(study_remote)
