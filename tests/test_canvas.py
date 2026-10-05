"""Tests for the network layer: pagination, auth, rate limits, downloads.

Everything is mocked — these tests never touch a real Canvas.
"""

from __future__ import annotations

import pytest
import responses
from responses import matchers

from canvas_reader.canvas import API, AuthExpired, Canvas, CanvasError, RateLimited, next_link

HOST = "https://school.instructure.com"


def _add(rsps, url, **kwargs):
    """Register a URL while ignoring its query string (endpoints use ?per_page=...)."""
    rsps.add(
        responses.GET, url, match=[matchers.query_param_matcher({}, strict_match=False)], **kwargs
    )
    return rsps


class TestNextLink:
    def test_extracts_next(self):
        header = (
            f'<{HOST}/api/v1/courses?page=1>; rel="current", '
            f'<{HOST}/api/v1/courses?page=2>; rel="next", '
            f'<{HOST}/api/v1/courses?page=9>; rel="last"'
        )
        assert next_link(header) == f"{HOST}/api/v1/courses?page=2"

    def test_missing_next(self):
        assert next_link(f'<{HOST}/x?page=1>; rel="first"') is None

    def test_empty(self):
        assert next_link("") is None

    def test_url_with_comma_survives(self):
        header = f'<{HOST}/x?ids=1,2&page=2>; rel="next"'
        assert next_link(header) == f"{HOST}/x?ids=1,2&page=2"


class TestPagination:
    @responses.activate
    def test_follows_link_header_to_the_end(self, client):
        _add(
            responses,
            f"{HOST}{API}/courses",
            json=[{"id": 1}],
            headers={"Link": f'<{HOST}{API}/courses?page=2>; rel="next"'},
        )
        _add(responses, f"{HOST}{API}/courses", json=[{"id": 2}, {"id": 3}])
        result = client.get_list(f"{API}/courses?per_page=100")
        assert [c["id"] for c in result] == [1, 2, 3]
        assert len(responses.calls) == 2

    @responses.activate
    def test_non_list_response_is_an_error(self, client):
        _add(responses, f"{HOST}{API}/courses", json={"errors": [{"message": "nope"}]})
        with pytest.raises(CanvasError, match="expected a list"):
            client.get_list(f"{API}/courses")


class TestAuthErrors:
    @responses.activate
    def test_401_cookie_mode_mentions_cookie(self, client):
        _add(responses, f"{HOST}{API}/users/self/profile", status=401, json={"errors": []})
        with pytest.raises(AuthExpired, match="cookie expired"):
            client.get_obj(f"{API}/users/self/profile")

    @responses.activate
    def test_401_token_mode_mentions_token(self, token_cfg):
        client = Canvas(token_cfg, sleep=lambda _: None)
        _add(responses, f"{HOST}{API}/users/self/profile", status=401, json={"errors": []})
        with pytest.raises(AuthExpired, match="access token"):
            client.get_obj(f"{API}/users/self/profile")

    @responses.activate
    def test_html_login_page_is_auth_expiry(self, client):
        _add(
            responses,
            f"{HOST}{API}/users/self/profile",
            body="<html><body>Log in</body></html>",
            content_type="text/html",
        )
        with pytest.raises(AuthExpired, match="login page"):
            client.get_obj(f"{API}/users/self/profile")

    @responses.activate
    def test_credentials_never_leave_canvas_host(self, client, cfg):
        # same host: auth attached …
        assert client._headers(f"{HOST}/anything") == {"Cookie": cfg.cookie}
        # any other host (CDN, S3): nothing
        assert client._headers("https://cdn.example.org/x") == {}
        assert client._headers("https://school.instructure.com.evil.example/x") == {}


class TestRateLimits:
    @responses.activate
    def test_403_rate_limit_backs_off_then_succeeds(self, client):
        body = "403 Forbidden (Rate Limit Exceeded)"
        _add(responses, f"{HOST}{API}/courses", status=403, body=body, content_type="text/plain")
        _add(responses, f"{HOST}{API}/courses", json=[{"id": 1}])
        result = client.get_list(f"{API}/courses")
        assert result == [{"id": 1}]
        assert client.sleeps == [2]

    @responses.activate
    def test_rate_limit_exhaustion_raises_typed_error(self, client):
        body = "429 Too Many Requests — Rate Limit Exceeded"
        for _ in range(6):
            _add(
                responses, f"{HOST}{API}/courses", status=429, body=body, content_type="text/plain"
            )
        with pytest.raises(RateLimited, match="wait a few minutes"):
            client.get_list(f"{API}/courses")
        assert len(client.sleeps) == 5  # 2, 5, 15, 60, 240

    @responses.activate
    def test_403_without_rate_limit_text_is_a_plain_error(self, client):
        _add(
            responses,
            f"{HOST}{API}/courses",
            status=403,
            json={"errors": [{"message": "forbidden"}]},
        )
        with pytest.raises(CanvasError, match="forbidden"):
            client.get_list(f"{API}/courses")
        assert client.sleeps == []

    @responses.activate
    def test_5xx_retries(self, client):
        _add(responses, f"{HOST}{API}/courses", status=500, body="boom", content_type="text/plain")
        _add(responses, f"{HOST}{API}/courses", json=[])
        assert client.get_list(f"{API}/courses") == []
        assert client.sleeps == [2]

    @responses.activate
    def test_low_quota_header_paces_requests(self, client):
        _add(
            responses,
            f"{HOST}{API}/users/self/profile",
            json={"id": 1, "name": "Sam"},
            headers={"X-Rate-Limit-Remaining": "3.0"},
        )
        client.get_obj(f"{API}/users/self/profile")
        assert client.sleeps == [2]


class TestErrorMessages:
    @responses.activate
    def test_canvas_error_shape_is_extracted(self, client):
        _add(
            responses,
            f"{HOST}{API}/courses/1",
            status=404,
            json={"errors": [{"message": "not found"}]},
        )
        with pytest.raises(CanvasError, match="not found") as excinfo:
            client.get_obj(f"{API}/courses/1")
        assert excinfo.value.status == 404
        assert f"[{API}/courses/1]" in str(excinfo.value)  # failing endpoint is named

    @responses.activate
    def test_bare_message_shape_is_extracted(self, client):
        _add(
            responses,
            f"{HOST}/api/v1/courses/1",
            status=404,
            json={"message": "domain not found"},
        )
        with pytest.raises(CanvasError, match="domain not found"):
            client.get_obj(f"{API}/courses/1")


class TestDownload:
    @responses.activate
    def test_redirect_hops_drop_auth_and_commit_file(self, client, cfg, tmp_path, tiny_pdf):
        cdn = "https://cdn.example.org/bucket/notes.pdf"
        responses.add(
            responses.GET, f"{HOST}/files/7/download", status=302, headers={"Location": cdn}
        )
        responses.add(responses.GET, cdn, body=tiny_pdf, content_type="application/pdf")

        dest = tmp_path / "notes.pdf"
        client.download(f"{HOST}/files/7/download", dest, magic=b"%PDF-")

        assert dest.read_bytes() == tiny_pdf
        hop1, hop2 = responses.calls
        assert hop1.request.headers["Cookie"] == cfg.cookie  # same host: auth sent
        assert "Cookie" not in hop2.request.headers  # cross-host: no credentials
        assert "Authorization" not in hop2.request.headers

    @responses.activate
    def test_magic_mismatch_refuses_the_file(self, client, tmp_path):
        responses.add(
            responses.GET,
            f"{HOST}/files/7/download",
            body=b"<html>gotcha</html>",
            content_type="application/pdf",
        )
        dest = tmp_path / "evil.pdf"
        with pytest.raises(CanvasError, match="not a valid file"):
            client.download(f"{HOST}/files/7/download", dest, magic=b"%PDF-")
        assert not dest.exists()
        assert not (tmp_path / "evil.pdf.part").exists()

    @responses.activate
    def test_login_page_html_is_detected(self, client, tmp_path):
        responses.add(
            responses.GET,
            f"{HOST}/files/7/download",
            body="<html>Log in</html>",
            content_type="text/html",
        )
        with pytest.raises(AuthExpired):
            client.download(f"{HOST}/files/7/download", tmp_path / "x.pdf", magic=b"%PDF-")
        assert not (tmp_path / "x.pdf").exists()

    @responses.activate
    def test_stale_verifier_surfaces_as_error_for_cli_retry(self, client, tmp_path):
        responses.add(
            responses.GET,
            f"{HOST}/files/7/download",
            status=403,
            body="403 Forbidden (Rate Limit Exceeded)",
            content_type="text/plain",
        )
        with pytest.raises(RateLimited):
            client.download(f"{HOST}/files/7/download", tmp_path / "x.pdf")
