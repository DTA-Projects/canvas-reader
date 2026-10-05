"""Tests for HTML → Markdown conversion, link rewriting, and PDF text extraction."""

from __future__ import annotations

from canvas_reader.convert import extract_pdf_text, html_to_markdown, rewrite_links

HOST = "https://school.instructure.com"


class TestHtmlToMarkdown:
    def test_headings_lists_and_emphasis(self):
        html = """
        <h2>Week 1</h2>
        <p>Hello <strong>world</strong> and <em>friends</em>.</p>
        <ul><li>first</li><li>second</li></ul>
        """
        md = html_to_markdown(html)
        assert "## Week 1" in md
        assert "**world**" in md
        assert "*friends*" in md
        assert "- first" in md
        assert "- second" in md

    def test_scripts_stripped(self):
        md = html_to_markdown('<p>hi</p><script>alert("x")</script>')
        assert "alert" not in md
        assert "hi" in md

    def test_entities_decoded(self):
        md = html_to_markdown("<p>don&#8217;t &mdash; stop</p>")
        assert "’" in md
        assert "—" in md
        assert "&#8217;" not in md

    def test_links_keep_their_href(self):
        md = html_to_markdown('<p><a href="https://example.org/x">click</a></p>')
        assert "[click](https://example.org/x)" in md

    def test_pre_blocks_survive(self):
        md = html_to_markdown("<pre>pip install canvas-reader</pre>")
        assert "pip install canvas-reader" in md
        assert "re>" not in md  # no leaked tag soup

    def test_empty_input(self):
        assert html_to_markdown("") == ""
        assert html_to_markdown("   ") == ""

    def test_no_triple_blank_lines(self):
        md = html_to_markdown("<p>a</p><p>b</p><p>c</p>")
        assert "\n\n\n" not in md


class TestRewriteLinks:
    def test_page_links_absolute_and_relative(self):
        text = f"See [notes]({HOST}/courses/1/pages/week-1) and [rel](/courses/1/pages/week-1)."
        out = rewrite_links(
            text, host=HOST, page_targets={"/courses/1/pages/week-1": "week-1.md"}, file_targets={}
        )
        assert "[notes](week-1.md)" in out
        assert "[rel](week-1.md)" in out

    def test_page_slug_prefix_collision(self):
        text = f"[a]({HOST}/courses/1/pages/foo) [b]({HOST}/courses/1/pages/foobar)"
        out = rewrite_links(
            text,
            host=HOST,
            page_targets={
                "/courses/1/pages/foo": "foo.md",
                "/courses/1/pages/foobar": "foobar.md",
            },
            file_targets={},
        )
        assert "(foo.md)" in out
        assert "(foobar.md)" in out

    def test_file_links_with_download_suffix_and_query(self):
        text = (
            f"[book]({HOST}/courses/1/files/9/download?verifier=abc%3D) "
            f"[img]({HOST}/files/9/preview) [other]({HOST}/files/91/download)"
        )
        out = rewrite_links(text, host=HOST, page_targets={}, file_targets={9: "../files/book.pdf"})
        assert "[book](../files/book.pdf)" in out
        assert "[img](../files/book.pdf)" in out
        assert "verifier" not in out
        # file id 91 is a different file and must stay untouched
        assert f"[other]({HOST}/files/91/download)" in out

    def test_unmatched_links_stay_absolute(self):
        text = f"[hw]({HOST}/courses/1/assignments/7)"
        out = rewrite_links(text, host=HOST, page_targets={}, file_targets={})
        assert out == text


class TestExtractPdfText:
    def test_sidecar_written(self, tmp_path, tiny_pdf):
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(tiny_pdf)
        sidecar = extract_pdf_text(pdf)
        assert sidecar is not None and sidecar.exists()
        assert sidecar.suffix == ".txt"

    def test_corrupt_pdf_returns_none_not_crash(self, tmp_path):
        pdf = tmp_path / "broken.pdf"
        pdf.write_bytes(b"%PDF-1.4 this is not really a pdf")
        assert extract_pdf_text(pdf) is None
