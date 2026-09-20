"""Reading a customer's own website, safely.

The questionnaire's "guess it" button takes a URL the customer types and has
the server fetch it. That is a request the customer controls, issued from
inside our network, which is the classic shape of a server-side request
forgery: ``http://169.254.169.254/`` reaches the cloud metadata service,
``http://localhost:9000`` reaches MinIO, ``http://postgres:5432`` reaches the
database.

So every hop is checked before it is made:

* the scheme must be http or https;
* the host must resolve only to public addresses — every address it resolves
  to, not just the first;
* redirects are followed by hand, re-checking each target;
* the response is capped in size and in time.

The extracted text goes into an LLM prompt, so it is also untrusted input. It
is labelled as such in the prompt and never treated as instructions.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 10.0
#: Enough for a model to understand what the brand does; more is noise.
MAX_TEXT_CHARS = 12_000

#: Stripped entirely — their contents are not page text.
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class WebsiteFetchError(RuntimeError):
    """The URL could not be read, with a reason safe to show the customer."""


@dataclass
class WebsiteContent:
    url: str
    title: str = ""
    description: str = ""
    text: str = ""
    headings: list[str] = field(default_factory=list)
    language: str | None = None

    def as_prompt_block(self) -> str:
        """Rendered for the brief assistant, clearly marked as external data."""
        parts = [f"Source: {self.url}"]
        if self.title:
            parts.append(f"Page title: {self.title}")
        if self.description:
            parts.append(f"Meta description: {self.description}")
        if self.headings:
            parts.append("Headings:\n" + "\n".join(f"- {h}" for h in self.headings[:25]))
        if self.text:
            parts.append(f"Page text:\n{self.text}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# address checks
# ---------------------------------------------------------------------------
def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # ``is_global`` alone misses some reserved ranges on older Pythons, so the
    # dangerous categories are named explicitly.
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_public_addresses(host: str) -> list[str]:
    """Every address ``host`` resolves to, refusing if any is not public.

    Checking all of them matters: a name that resolves to one public address
    and one loopback address must be refused, not half-allowed.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebsiteFetchError(f"could not resolve {host!r}") from exc

    # getaddrinfo's sockaddr is (host, port) for IPv4 and a 4-tuple for IPv6;
    # the address is the first element either way, and always a string.
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise WebsiteFetchError(f"could not resolve {host!r}")

    private = [address for address in addresses if not _is_public(address)]
    if private:
        raise WebsiteFetchError(
            f"{host!r} resolves to an address that is not on the public internet "
            f"({private[0]}); only public websites can be read"
        )
    return addresses


def validate_url(url: str) -> str:
    """Check a URL is one we are willing to fetch, returning it normalised."""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise WebsiteFetchError("the address must start with http:// or https://")
    if not parsed.hostname:
        raise WebsiteFetchError("the address has no host")
    resolve_public_addresses(parsed.hostname)
    return parsed.geturl()


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------
class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.language: str | None = None
        self.headings: list[str] = []
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag == "html" and "lang" in attributes:
            self.language = attributes["lang"][:8] or None
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in {"h1", "h2", "h3"}:
            self._heading = ""
        elif tag == "meta":
            name = attributes.get("name", "").lower() or attributes.get("property", "").lower()
            if name in {"description", "og:description"} and not self.description:
                self.description = attributes.get("content", "").strip()[:500]

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3"}:
            if self._heading and self._heading.strip():
                self.headings.append(self._heading.strip()[:200])
            self._heading = None

    def handle_data(self, data: str) -> None:
        # ``head`` is in SKIP_TAGS, so the title is read through its own flag.
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        if self._heading is not None:
            self._heading += data
        text = data.strip()
        if text:
            self._chunks.append(text)

    @property
    def text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def extract(html: str, url: str) -> WebsiteContent:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - malformed HTML is expected
        logger.info("html parse gave up early", extra={"url": url, "error": str(exc)})

    return WebsiteContent(
        url=url,
        title=parser.title.strip()[:300],
        description=parser.description,
        text=parser.text[:MAX_TEXT_CHARS],
        headings=parser.headings,
        language=parser.language,
    )


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> WebsiteContent:
    """Read a public web page, following redirects by hand.

    Redirects are not delegated to the HTTP client because each hop is a new
    address the customer controls: a public URL that redirects to
    ``http://169.254.169.254/`` must be refused at the second hop, not the
    first.
    """
    import httpx

    current = validate_url(url)
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = client.get(
                    current,
                    headers={
                        "user-agent": "ai-marketing-agent/0.1 (brand brief assistant)",
                        "accept": "text/html,application/xhtml+xml",
                    },
                )
            except httpx.HTTPError as exc:
                raise WebsiteFetchError(
                    f"could not reach the address: {type(exc).__name__}"
                ) from exc

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise WebsiteFetchError("the site redirected without saying where")
                current = validate_url(urljoin(current, location))
                continue

            if response.status_code >= 400:
                raise WebsiteFetchError(f"the site answered with status {response.status_code}")

            content_type = response.headers.get("content-type", "")
            if content_type and "html" not in content_type.lower():
                raise WebsiteFetchError(
                    f"the address is not a web page (content type {content_type!r})"
                )

            body = response.content[:MAX_BYTES]
            return extract(body.decode(response.encoding or "utf-8", errors="replace"), current)

    raise WebsiteFetchError("the site redirected too many times")
