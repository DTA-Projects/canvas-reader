"""End-to-end CLI tests against a fully mocked Canvas (offline, fast)."""

from __future__ import annotations

import json

import pytest
import responses
from responses import matchers

from canvas_reader.cli import main
from canvas_reader.store import load_manifest

HOST = "https://school.instructure.com"


def _add(rsps, url, **kwargs):
    rsps.add(
        responses.GET,
        url,
        match=[matchers.query_param_matcher({}, strict_match=False)],
        **kwargs,
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean environment pointing at a temp content dir."""
    monkeypatch.chdir(tmp_path)  # no .env leakage from the repo
    monkeypatch.setenv("CANVAS_HOST", HOST)
    monkeypatch.setenv("CANVAS_SESSION", "session-cookie-value")
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    monkeypatch.setenv("CONTENT_DIR", str(tmp_path / "content"))
    return tmp_path


COURSE = {
    "id": 101,
    "name": "Sample Course",
    "course_code": "BIO-101",
    "html_url": f"{HOST}/courses/101",
}

PAGE = {
    "url": "week-1",
    "title": "Week 1",
    "updated_at": "2026-09-01T12:00:00Z",
    "body": (
        "<h2>Week 1</h2>"
        f'<p>Read the <a href="{HOST}/files/55/download">textbook</a> '
        f'and the <a href="{HOST}/courses/101/pages/week-2">next page</a>.</p>'
    ),
}

MODULE = {
    "id": 7,
    "name": "Week 1",
    "items": [
        {"type": "SubHeader", "title": "Getting started"},
        {"type": "Page", "title": "Week 1 notes", "page_url": "week-1"},
        {"type": "File", "title": "Textbook", "content_id": 55},
        {"type": "Assignment", "title": "HW 1", "html_url": f"{HOST}/courses/101/assignments/7"},
    ],
}


def _mock_canvas(rsps, *, tiny_pdf):
    _add(rsps, f"{HOST}/api/v1/courses", json=[COURSE])
    _add(
        rsps,
        f"{HOST}/api/v1/courses/101/files",
        json=[
            {
                "id": 55,
                "display_name": "Sample Book.pdf",
                "size": len(tiny_pdf),
                "url": f"{HOST}/files/55/download",
            }
        ],
    )
    _add(rsps, f"{HOST}/files/55/download", body=tiny_pdf, content_type="application/pdf")
    _add(
        rsps,
        f"{HOST}/api/v1/courses/101/pages",
        json=[
            PAGE,
            {"url": "week-2", "body": "<p>More.</p>", "updated_at": "2026-09-02T00:00:00Z"},
        ],
    )
    _add(
        rsps,
        f"{HOST}/api/v1/courses/101",
        json={**COURSE, "syllabus_body": "<p>Welcome to BIO-101.</p>"},
    )
    _add(rsps, f"{HOST}/api/v1/courses/101/modules", json=[MODULE])


class TestWhoami:
    @responses.activate
    def test_success(self, env, capsys):
        _add(
            responses,
            f"{HOST}/api/v1/users/self/profile",
            json={"id": 1, "name": "Sam Student", "login_id": "sam@x.edu"},
        )
        assert main(["whoami"]) == 0
        out = capsys.readouterr().out
        assert "Sam Student" in out
        assert "session" in out

    @responses.activate
    def test_expired_cookie_exits_2(self, env, capsys):
        _add(responses, f"{HOST}/api/v1/users/self/profile", status=401, json={"errors": []})
        assert main(["whoami"]) == 2
        assert "cookie expired" in capsys.readouterr().err

    @responses.activate
    def test_unreachable_canvas_exits_1_not_traceback(self, env, capsys):
        """No registrations → connection error must come back as a friendly message."""
        assert main(["whoami"]) == 1
        assert "cannot reach Canvas" in capsys.readouterr().err

    def test_missing_config_exits_3(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        for var in (
            "CANVAS_HOST",
            "CANVAS_API_TOKEN",
            "CANVAS_SESSION",
            "CONTENT_DIR",
            "TEXTBOOKS_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        assert main(["whoami"]) == 3
        assert "CANVAS_HOST" in capsys.readouterr().err

    @responses.activate
    def test_placeholder_token_exits_3(self, env, monkeypatch, capsys):
        monkeypatch.setenv("CANVAS_API_TOKEN", "PASTE_TOKEN_HERE")
        monkeypatch.delenv("CANVAS_SESSION")
        assert main(["whoami"]) == 3
        assert "placeholder" in capsys.readouterr().err


class TestList:
    @responses.activate
    def test_json_output(self, env, capsys):
        _add(responses, f"{HOST}/api/v1/courses", json=[COURSE])
        assert main(["list", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data == [{"id": 101, "code": "BIO-101", "name": "Sample Course"}]


class TestSync:
    @responses.activate
    def test_full_sync_builds_local_corpus(self, env, tiny_pdf, capsys):
        _mock_canvas(responses, tiny_pdf=tiny_pdf)
        assert main(["sync", "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [
            {
                "id": 101,
                "name": "Sample Course",
                "slug": "101-sample-course",
                "pages": 2,
                "files": 1,
                "warnings": [],
            }
        ]

        course_dir = env / "content" / "courses" / "101-sample-course"

        readme = (course_dir / "README.md").read_text(encoding="utf-8")
        assert readme.startswith("# Sample Course")
        assert "Welcome to BIO-101." in readme
        assert "[Modules](modules.md)" in readme

        week1 = (course_dir / "pages" / "week-1.md").read_text(encoding="utf-8")
        assert "## Week 1" in week1
        assert "(../files/sample-book.pdf)" in week1  # textbook link made local
        assert "(week-2.md)" in week1  # page link made local

        modules = (course_dir / "modules.md").read_text(encoding="utf-8")
        assert "### Getting started" in modules
        assert "[Week 1 notes](pages/week-1.md)" in modules
        assert "[Textbook](files/sample-book.pdf)" in modules
        assert f"[HW 1]({HOST}/courses/101/assignments/7)" in modules  # external stays absolute

        pdf = course_dir / "files" / "sample-book.pdf"
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert pdf.with_suffix(".txt").exists()

        manifest = load_manifest(env / "content")
        assert manifest["courses"]["101"]["name"] == "Sample Course"
        assert manifest["courses"]["101"]["synced_at"]

    @responses.activate
    def test_second_sync_skips_unchanged_downloads(self, env, tiny_pdf, capsys):
        _mock_canvas(responses, tiny_pdf=tiny_pdf)
        assert main(["sync"]) == 0
        capsys.readouterr()
        responses.calls.reset()
        _mock_canvas(responses, tiny_pdf=tiny_pdf)  # same data again
        assert main(["sync"]) == 0
        requested = [c.request.url for c in responses.calls]
        assert not any("/files/55/download" in url for url in requested)  # PDF not re-fetched

    @responses.activate
    def test_course_filter(self, env, tiny_pdf, capsys):
        _mock_canvas(responses, tiny_pdf=tiny_pdf)
        assert main(["sync", "BIO"]) == 0
        assert "Sample Course" in capsys.readouterr().out

    @responses.activate
    def test_unknown_course_exits_1(self, env, capsys):
        _add(responses, f"{HOST}/api/v1/courses", json=[COURSE])
        assert main(["sync", "nope"]) == 1
        assert "no course matches" in capsys.readouterr().err

    @responses.activate
    def test_books_only_downloads_pdfs(self, env, tiny_pdf, capsys):
        _mock_canvas(responses, tiny_pdf=tiny_pdf)
        assert main(["sync", "--books-only"]) == 0
        course_dir = env / "content" / "courses" / "101-sample-course"
        assert (course_dir / "files" / "sample-book.pdf").exists()
        assert not (course_dir / "pages").exists()
        assert not (course_dir / "README.md").exists()


class TestRestrictedCourse:
    """Schools can hide the Files/Pages nav tabs — sync must degrade, not die."""

    @staticmethod
    def _mock_restricted(rsps, tiny_pdf):
        _add(rsps, f"{HOST}/api/v1/courses", json=[COURSE])
        _add(
            rsps,
            f"{HOST}/api/v1/courses/101",
            json={
                **COURSE,
                "syllabus_body": f'<p>Get the <a href="{HOST}/files/66/download">handout</a>.</p>',
            },
        )
        _add(
            rsps,
            f"{HOST}/api/v1/courses/101/modules",
            json=[
                {
                    "id": 7,
                    "name": "Week 1",
                    "items": [
                        {"type": "Page", "title": "Week 1", "page_url": "week-1"},
                        {"type": "File", "title": "Book", "content_id": 55},
                    ],
                }
            ],
        )
        # listings denied, exactly as when the nav tabs are hidden …
        _add(
            rsps,
            f"{HOST}/api/v1/courses/101/pages",
            status=403,
            json={"errors": [{"message": "That page has been disabled for this course"}]},
        )
        _add(
            rsps,
            f"{HOST}/api/v1/courses/101/files",
            status=403,
            json={"errors": [{"message": "user not authorized to perform that action"}]},
        )
        # … but single-object endpoints still work
        _add(
            rsps,
            f"{HOST}/api/v1/courses/101/pages/week-1",
            json={
                "url": "week-1",
                "title": "Week 1",
                "updated_at": "2026-09-01T12:00:00Z",
                "body": f'<p>Read <a href="{HOST}/files/55/download">the book</a>.</p>',
            },
        )
        _add(
            rsps,
            f"{HOST}/api/v1/files/55",
            json={
                "id": 55,
                "display_name": "Sample Book.pdf",
                "size": len(tiny_pdf),
                "url": f"{HOST}/files/55/download",
            },
        )
        _add(
            rsps,
            f"{HOST}/api/v1/files/66",
            json={
                "id": 66,
                "display_name": "Handout.docx",
                "size": 9,
                "url": f"{HOST}/files/66/download",
            },
        )
        _add(rsps, f"{HOST}/files/55/download", body=tiny_pdf, content_type="application/pdf")
        _add(
            rsps,
            f"{HOST}/files/66/download",
            body=b"fake-docx",
            content_type="application/octet-stream",
        )

    @responses.activate
    def test_falls_back_and_still_syncs(self, env, tiny_pdf, capsys):
        self._mock_restricted(responses, tiny_pdf)
        assert main(["sync", "--json"]) == 0
        captured = capsys.readouterr()

        result = json.loads(captured.out)[0]
        assert result["pages"] == 1
        assert result["files"] == 2  # module-linked book + syllabus-scraped handout
        assert len(result["warnings"]) >= 2  # both denials reported, not fatal
        assert "⚠" in captured.err  # and surfaced to the human

        course_dir = env / "content" / "courses" / "101-sample-course"
        week1 = (course_dir / "pages" / "week-1.md").read_text(encoding="utf-8")
        assert "(../files/sample-book.pdf)" in week1
        assert (course_dir / "files" / "sample-book.pdf").exists()
        assert (course_dir / "files" / "handout.docx").exists()
        assert (course_dir / "README.md").exists()


class TestStatus:
    @responses.activate
    def test_status_after_sync(self, env, tiny_pdf, capsys):
        _mock_canvas(responses, tiny_pdf=tiny_pdf)
        main(["sync"])
        capsys.readouterr()
        assert main(["status", "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["id"] == 101
        assert rows[0]["pages"] == 2
        assert rows[0]["pdfs"] == 1

    def test_status_empty(self, env, capsys):
        assert main(["status"]) == 0
        assert "Nothing synced yet" in capsys.readouterr().out
