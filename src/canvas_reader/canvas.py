"""Canvas REST API client: authentication, pagination, rate limits, downloads.

The whole network layer lives here — roughly 200 lines, no framework.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from . import __version__
from .store import Config, atomic_output

API = "/api/v1"

BACKOFF = [2, 5, 15, 60, 240]  # seconds between retries
RATE_LIMIT_FLOOR = 5  # pause when the remaining quota drops below this
MAX_REDIRECTS = 6


class CanvasError(Exception):
    """An API error we could not classify further."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthExpired(CanvasError):
    """HTTP 401, or a login page instead of data: token revoked / cookie expired."""


class RateLimited(CanvasError):
    """Canvas said "Rate Limit Exceeded" and we ran out of retries."""


_LINK_RE = re.compile(r"<([^>]+)>\s*;\s*rel=\"([^\"]+)\"")


def next_link(header: str) -> str | None:
    """Pull rel="next" out of a Link response header (case-insensitive rel)."""
    for url, rel in _LINK_RE.findall(header or ""):
        if rel.lower() == "next":
            return url
    return None


def _error_message(response: requests.Response) -> str:
    """Extract Canvas's {"errors": [{"message": ...}]} shape, falling back to text."""
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        return data["message"]  # e.g. {"message": "domain not found"}
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list):
        parts = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]
        return "; ".join(parts) or str(data)[:300]
    if isinstance(errors, dict):
        return "; ".join(f"{k}: {v}" for k, v in errors.items())
    return str(data)[:300]


class Canvas:
    """A tiny Canvas client. Sequential by design — Canvas throttles concurrency."""

    def __init__(self, cfg: Config, sleep=time.sleep, timeout: float | tuple = (10, 120)) -> None:
        self.cfg = cfg
        self._sleep = sleep
        self._timeout = timeout
        self.http = requests.Session()
        self.http.headers["User-Agent"] = f"canvas-reader/{__version__}"

    # -- auth ----------------------------------------------------------------

    def _headers(self, url: str) -> dict[str, str]:
        """Attach credentials only to our own Canvas host — never to CDNs or lookalikes.

        The boundary check requires an exact origin match, so neither
        `school.instructure.com.evil.example` nor a scheme change can qualify.
        """
        if not (url == self.cfg.host or url.startswith(self.cfg.host + "/")):
            return {}
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        if self.cfg.cookie:
            return {"Cookie": self.cfg.cookie}
        return {}

    def _auth_error(self, detail: str) -> AuthExpired:
        if self.cfg.token:
            return AuthExpired(
                f"access token rejected ({detail}) — regenerate it in Canvas: "
                "Account → Profile → Approved Integrations, then update .env"
            )
        return AuthExpired(
            f"session cookie expired ({detail}) — copy a fresh _canvas_session cookie "
            "(F12 → Application → Cookies) and update .env"
        )

    # -- requests ------------------------------------------------------------

    @staticmethod
    def _is_rate_limit(response: requests.Response) -> bool:
        return response.status_code in (403, 429) and "rate limit" in (response.text or "").lower()

    def _get(self, url: str) -> requests.Response:
        """GET with auth, honest error classes, and backoff. Returns a JSON response."""
        attempt = 0
        while True:
            response = self.http.get(url, headers=self._headers(url), timeout=self._timeout)
            if response.status_code == 401:
                raise self._auth_error("HTTP 401")
            if self._is_rate_limit(response):
                if attempt >= len(BACKOFF):
                    raise RateLimited(
                        "Canvas is rate limiting us — wait a few minutes and rerun "
                        "(sync resumes where it stopped)"
                    )
                self._sleep(BACKOFF[attempt])
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= len(BACKOFF):
                    raise CanvasError(
                        f"Canvas server error: HTTP {response.status_code}", response.status_code
                    )
                self._sleep(BACKOFF[attempt])
                attempt += 1
                continue
            if not response.ok:
                raise CanvasError(_error_message(response), response.status_code)
            if "text/html" in response.headers.get("Content-Type", ""):
                raise self._auth_error("got a login page instead of JSON")
            self._respect_quota(response)
            return response

    def _respect_quota(self, response: requests.Response) -> None:
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if remaining:
            try:
                if float(remaining) < RATE_LIMIT_FLOOR:
                    self._sleep(2)
            except ValueError:
                pass

    # -- endpoints -----------------------------------------------------------

    def get_list(self, path: str) -> list[dict]:
        """GET a collection, following Link-header pagination to the end."""
        url = self.cfg.host + path
        items: list[dict] = []
        while url:
            response = self._get(url)
            page = response.json()
            if not isinstance(page, list):
                raise CanvasError(f"expected a list from {path}, got {type(page).__name__}")
            items.extend(page)
            url = next_link(response.headers.get("Link", ""))
        return items

    def get_obj(self, path: str) -> dict:
        data = self._get(self.cfg.host + path).json()
        if not isinstance(data, dict):
            raise CanvasError(f"expected an object from {path}, got {type(data).__name__}")
        return data

    def download(self, url: str, dest: Path, magic: bytes | None = None) -> None:
        """Download a file, following redirects BY HAND.

        Canvas 302s to a pre-signed CDN/S3 URL. Auth headers are rebuilt per hop
        from _headers(), so credentials never leave the Canvas host. HTML responses
        (login pages) are refused, and `magic` bytes are verified before the file
        is committed into place.
        """
        for _ in range(MAX_REDIRECTS):
            response = self.http.get(
                url,
                headers=self._headers(url),
                allow_redirects=False,
                stream=True,
                timeout=self._timeout,
            )
            if response.is_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise CanvasError("redirect without a Location header")
                url = urljoin(url, location)
                continue

            try:
                if response.status_code == 401:
                    raise self._auth_error("HTTP 401 on download")
                if self._is_rate_limit(response):
                    raise RateLimited(
                        "Canvas rate limited a file download — wait a few minutes "
                        "and rerun (sync resumes)"
                    )
                if not response.ok:
                    raise CanvasError(
                        f"download failed: HTTP {response.status_code}", response.status_code
                    )
                if "text/html" in response.headers.get("Content-Type", ""):
                    raise self._auth_error("download returned a login page")
                with atomic_output(dest) as tmp:
                    with open(tmp, "wb") as fh:
                        for chunk in response.iter_content(chunk_size=1 << 16):
                            if chunk:
                                fh.write(chunk)
                    if magic:
                        head = tmp.read_bytes()[: len(magic)]
                        if head != magic:
                            want = magic.decode("ascii", "replace")
                            raise CanvasError(
                                f"expected {want} data, got {head!r} — not a valid file"
                            )
            finally:
                response.close()
            return
        raise CanvasError("too many redirects while downloading")
