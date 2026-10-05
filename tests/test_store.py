"""Tests for local concerns: config, filenames, manifest, atomic writes."""

from __future__ import annotations

import json

import pytest

from canvas_reader.store import (
    ConfigError,
    atomic_output,
    cookie_header,
    load_config,
    load_manifest,
    safe_filename,
    save_manifest,
    slugify,
    write_env_file,
    write_text,
)


class TestSlugify:
    def test_spaces_and_punctuation(self):
        assert slugify("Week 1: Intro & Review!") == "week-1-intro-review"

    def test_path_traversal_is_stripped(self):
        assert slugify("../../etc/passwd") == "etc-passwd"

    def test_windows_reserved_name(self):
        assert slugify("CON") == "con-file"
        assert slugify("lpt1") == "lpt1-file"

    def test_empty_becomes_untitled(self):
        assert slugify("") == "untitled"
        assert slugify("日本語のみ") == "untitled"  # non-ascii collapses safely

    def test_length_cap(self):
        assert len(slugify("x" * 500)) == 80

    def test_no_trailing_dashes(self):
        assert slugify("  --hello--  ") == "hello"


class TestSafeFilename:
    def test_keeps_extension_lowercased(self):
        assert safe_filename("Ch1: Notes.PDF") == "ch1-notes.pdf"

    def test_strips_directory_components(self):
        assert safe_filename("../../evil.sh") == "evil.sh"
        assert safe_filename("..\\..\\evil.sh") == "evil.sh"

    def test_uses_fallback_for_extension_only_names(self):
        assert safe_filename(".pdf", fallback="file-9") == "file-9.pdf"

    def test_multiple_dots(self):
        assert safe_filename("Lecture.v2.final.pdf") == "lecture-v2-final.pdf"


class TestCookieHeader:
    def test_bare_value_gets_cookie_name(self):
        assert cookie_header("eyJ2Ijox") == "_canvas_session=eyJ2Ijox"

    def test_bare_value_with_base64_padding(self):
        assert cookie_header("abc=") == "_canvas_session=abc="

    def test_full_cookie_passthrough(self):
        assert cookie_header("_canvas_session=abc=; other=1") == "_canvas_session=abc=; other=1"

    def test_whitespace_and_quotes_removed(self):
        assert cookie_header('  "abc"  ') == "_canvas_session=abc"


class TestLoadConfig:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # no stray .env
        for var in (
            "CANVAS_HOST",
            "CANVAS_API_TOKEN",
            "CANVAS_SESSION",
            "CONTENT_DIR",
            "TEXTBOOKS_DIR",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_missing_everything(self):
        with pytest.raises(ConfigError, match="CANVAS_HOST"):
            load_config()

    def test_host_gets_scheme_and_slash_trimmed(self, monkeypatch):
        monkeypatch.setenv("CANVAS_HOST", "school.instructure.com/")
        monkeypatch.setenv("CANVAS_SESSION", "abc")
        cfg = load_config()
        assert cfg.host == "https://school.instructure.com"
        assert cfg.cookie == "_canvas_session=abc"
        assert cfg.auth_mode == "session"

    def test_placeholder_token_rejected(self, monkeypatch):
        monkeypatch.setenv("CANVAS_HOST", "https://s.instructure.com")
        monkeypatch.setenv("CANVAS_API_TOKEN", "PASTE_THE_TOKEN_HERE")
        with pytest.raises(ConfigError, match="placeholder"):
            load_config()

    def test_credentials_required(self, monkeypatch):
        monkeypatch.setenv("CANVAS_HOST", "https://s.instructure.com")
        with pytest.raises(ConfigError, match="no credentials"):
            load_config()

    def test_token_wins_when_both_set(self, monkeypatch):
        monkeypatch.setenv("CANVAS_HOST", "https://s.instructure.com")
        monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
        monkeypatch.setenv("CANVAS_SESSION", "abc")
        cfg = load_config()
        assert cfg.auth_mode == "token"
        assert cfg.token == "tok"

    def test_content_dir_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CANVAS_HOST", "https://s.instructure.com")
        monkeypatch.setenv("CANVAS_SESSION", "abc")
        monkeypatch.setenv("CONTENT_DIR", str(tmp_path / "elsewhere"))
        assert load_config().content_dir == tmp_path / "elsewhere"

    def test_textbooks_dir_is_optional(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CANVAS_HOST", "https://s.instructure.com")
        monkeypatch.setenv("CANVAS_SESSION", "abc")
        assert load_config().textbooks_dir is None
        monkeypatch.setenv("TEXTBOOKS_DIR", str(tmp_path / "Textbooks"))
        assert load_config().textbooks_dir == tmp_path / "Textbooks"

    def test_dotenv_file_is_read_from_working_directory(self, monkeypatch, tmp_path):
        """Regression: load_dotenv() alone walks from site-packages, not cwd."""
        (tmp_path / ".env").write_text(
            'CANVAS_HOST="https://real.instructure.com"\nCANVAS_SESSION="abc123"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        cfg = load_config()
        assert cfg.host == "https://real.instructure.com"
        assert cfg.cookie == "_canvas_session=abc123"


class TestManifest:
    def test_roundtrip(self, tmp_path):
        manifest = {"courses": {"101": {"name": "Bio"}}}
        save_manifest(tmp_path, manifest)
        assert load_manifest(tmp_path) == manifest

    def test_missing_returns_empty(self, tmp_path):
        assert load_manifest(tmp_path) == {"courses": {}}

    def test_corrupt_returns_empty_not_crash(self, tmp_path):
        (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
        assert load_manifest(tmp_path) == {"courses": {}}

    def test_keys_stay_strings(self, tmp_path):
        save_manifest(tmp_path, {"courses": {"101": {}}})
        raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert list(raw["courses"]) == ["101"]


class TestWrites:
    def test_write_text_uses_lf(self, tmp_path):
        write_text(tmp_path / "a" / "b.md", "line1\nline2\n")
        raw = (tmp_path / "a" / "b.md").read_bytes()
        assert b"\r" not in raw

    def test_atomic_output_cleans_up_on_failure(self, tmp_path):
        dest = tmp_path / "file.txt"
        with pytest.raises(ValueError):
            with atomic_output(dest) as tmp:
                tmp.write_text("partial", encoding="utf-8")
                raise ValueError("boom")
        assert not dest.exists()
        assert not (tmp_path / "file.txt.part").exists()

    def test_atomic_output_replaces_existing(self, tmp_path):
        dest = tmp_path / "file.txt"
        dest.write_text("old", encoding="utf-8")
        with atomic_output(dest) as tmp:
            tmp.write_text("new", encoding="utf-8")
        assert dest.read_text(encoding="utf-8") == "new"

    def test_env_file_written_quoted(self, tmp_path):
        path = tmp_path / ".env"
        write_env_file(path, "https://s.instructure.com", "tok", None)
        text = path.read_text(encoding="utf-8")
        assert 'CANVAS_HOST="https://s.instructure.com"' in text
        assert 'CANVAS_API_TOKEN="tok"' in text
        assert "CANVAS_SESSION" not in text
