"""Reading a customer-supplied URL.

The "guess it" button has the server fetch an address the customer typed,
which is the classic shape of a server-side request forgery. Most of what is
worth testing here is what the fetcher refuses.
"""

from __future__ import annotations

import pytest

from app.services.website import (
    WebsiteFetchError,
    extract,
    resolve_public_addresses,
    validate_url,
)

BLOCKED = [
    ("http://localhost:9000/", "MinIO"),
    ("http://127.0.0.1/", "loopback"),
    ("http://[::1]/", "IPv6 loopback"),
    ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ("http://10.0.0.5/", "private range"),
    ("http://192.168.1.1/", "home router"),
    ("http://172.16.0.1/", "private range"),
    ("http://0.0.0.0/", "unspecified"),
]


@pytest.mark.parametrize(("url", "what"), BLOCKED, ids=[what for _, what in BLOCKED])
def test_internal_addresses_are_refused(url: str, what: str) -> None:
    with pytest.raises(WebsiteFetchError):
        validate_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://example.com/"])
def test_only_http_is_allowed(url: str) -> None:
    with pytest.raises(WebsiteFetchError) as excinfo:
        validate_url(url)
    assert "http" in str(excinfo.value)


def test_a_url_without_a_host_is_refused() -> None:
    with pytest.raises(WebsiteFetchError):
        validate_url("http:///no-host")


def test_a_name_that_does_not_resolve_is_refused() -> None:
    with pytest.raises(WebsiteFetchError):
        resolve_public_addresses("no-such-host.invalid")


def test_a_name_resolving_to_both_public_and_private_is_refused(monkeypatch) -> None:
    """Half-allowed is not a state this can be in."""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(WebsiteFetchError) as excinfo:
        resolve_public_addresses("sneaky.example.com")
    assert "127.0.0.1" in str(excinfo.value)


def test_a_public_address_is_allowed(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    assert resolve_public_addresses("example.com") == ["93.184.216.34"]
    assert validate_url("https://example.com/about") == "https://example.com/about"


def test_a_redirect_into_the_private_network_is_refused(monkeypatch) -> None:
    """A public URL that redirects to the metadata service must fail at hop two."""
    import socket

    import httpx

    from app.services import website

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers=None):
            return httpx.Response(
                302,
                headers={"location": "http://metadata.internal/"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(WebsiteFetchError) as excinfo:
        website.fetch("https://example.com")
    assert "public internet" in str(excinfo.value)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------
HTML = """<html lang="fa">
<head>
  <title>آکمه — ابزار صنعتی</title>
  <meta name="description" content="ابزار برقی برای کارگاه‌های کوچک">
  <script>var secret = "must not appear";</script>
  <style>body { color: red }</style>
</head>
<body>
  <h1>دریل برقی</h1>
  <p>ما ابزار می‌سازیم.</p>
  <h2>خدمات</h2>
  <noscript>javascript off</noscript>
</body></html>"""


def test_the_page_is_reduced_to_text_and_metadata() -> None:
    content = extract(HTML, "https://example.com")
    assert content.title == "آکمه — ابزار صنعتی"
    assert content.description == "ابزار برقی برای کارگاه‌های کوچک"
    assert content.language == "fa"
    assert content.headings == ["دریل برقی", "خدمات"]
    assert "ما ابزار می‌سازیم." in content.text


def test_scripts_styles_and_noscript_are_stripped() -> None:
    """Otherwise the prompt fills up with JavaScript instead of page text."""
    content = extract(HTML, "https://example.com")
    assert "must not appear" not in content.text
    assert "color: red" not in content.text
    assert "javascript off" not in content.text


def test_the_extracted_text_is_capped() -> None:
    from app.services.website import MAX_TEXT_CHARS

    huge = "<html><body>" + ("word " * 50_000) + "</body></html>"
    assert len(extract(huge, "https://example.com").text) <= MAX_TEXT_CHARS


def test_malformed_html_does_not_raise() -> None:
    content = extract("<html><body><p>unclosed", "https://example.com")
    assert "unclosed" in content.text


def test_the_prompt_block_names_its_source() -> None:
    block = extract(HTML, "https://example.com").as_prompt_block()
    assert "https://example.com" in block
    assert "Page title:" in block
