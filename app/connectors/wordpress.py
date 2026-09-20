"""WordPress via the REST API and an Application Password (handoff section 10).

An Application Password is not the site's admin login: it is created under
*Users → Profile → Application Passwords*, scoped to one integration, and
revocable on its own without touching the account. WordPress accepts it over
HTTP Basic auth against the same endpoints a logged-in user would use.

SEO fields are best-effort. Yoast and Rank Math both write their title and
description to post meta, but neither exposes those keys through the REST
API by default — a site has to register them with ``show_in_rest`` (a small
mu-plugin, or a recent-enough version of either plugin with REST support
turned on) before this connector's values actually take. Sending them costs
nothing when the site ignores them: WordPress silently drops meta the caller
has no registered permission to write, rather than erroring, so this never
blocks a publish. ``seo_plugin: "none"`` skips the attempt outright, so a log
of what was tried always matches what the credential says the site supports.

hreflang via WPML or Polylang, mentioned in the same line of the handoff, is
plugin-specific enough that it needs a real site to build against — not
attempted here; see ``docs/publishing.md``.
"""

from __future__ import annotations

import logging

import httpx

from app.connectors.base import ConnectorError, CredentialPayload, PublishContent, PublishResult
from app.connectors.credentials import WordPressCredential
from app.connectors.markdown_render import render_answer_blocks, render_sections
from app.db.enums import Channel

logger = logging.getLogger(__name__)

#: Meta keys each SEO plugin looks for. Both are standard and stable across
#: recent versions of either plugin.
_SEO_META_KEYS = {
    "yoast": {"title": "_yoast_wpseo_title", "description": "_yoast_wpseo_metadesc"},
    "rankmath": {"title": "rank_math_title", "description": "rank_math_description"},
}

_TIMEOUT = 30.0


class WordPressConnector:
    channel = Channel.WORDPRESS

    def __init__(self, client: httpx.Client | None = None) -> None:
        # Tests inject a client built on httpx.MockTransport; production
        # gets a real one per call, scoped to the credential's site.
        self._client = client

    def publish(self, content: PublishContent, credential: CredentialPayload) -> PublishResult:
        if not isinstance(credential, WordPressCredential):
            raise ConnectorError(
                f"WordPressConnector needs a WordPressCredential, got {type(credential).__name__}"
            )
        base_url = str(credential.site_url).rstrip("/")
        auth = (credential.username, credential.application_password)
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None

        try:
            featured_media_id = None
            if content.media:
                featured_media_id = self._upload_media(client, base_url, auth, content.media[0])

            post = self._create_post(client, base_url, auth, content, credential, featured_media_id)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"WordPress request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        return PublishResult(
            external_id=str(post["id"]),
            external_url=post.get("link"),
            details={"status": post.get("status"), "featured_media": featured_media_id},
        )

    def _upload_media(self, client, base_url, auth, media) -> int:
        response = client.post(
            f"{base_url}/wp-json/wp/v2/media",
            auth=auth,
            content=media.data,
            headers={
                "Content-Type": media.mime_type,
                "Content-Disposition": f'attachment; filename="{media.filename}"',
            },
        )
        _raise_for_wp_error(response, "uploading the featured image")
        media_id = response.json()["id"]

        if media.alt_text:
            # Alt text is a separate write: the upload endpoint does not
            # accept it in the same multipart request.
            alt_response = client.post(
                f"{base_url}/wp-json/wp/v2/media/{media_id}",
                auth=auth,
                json={"alt_text": media.alt_text},
            )
            _raise_for_wp_error(alt_response, "setting the image's alt text")
        return media_id

    def _create_post(self, client, base_url, auth, content, credential, featured_media_id) -> dict:
        article = content.article or {}
        title = article.get("title") or content.title
        slug = article.get("slug")

        body_html = render_sections(article.get("sections") or [])
        answer_blocks_html = render_answer_blocks(
            article.get("answer_blocks") or [], "سوالات متداول"
        )
        if not body_html:
            # No article was assembled for this package — fall back to the
            # variant's own short body rather than publishing an empty post.
            body_html = f"<p>{content.body}</p>"
        content_html = "\n".join(part for part in (body_html, answer_blocks_html) if part)

        payload: dict = {
            "title": title,
            "content": content_html,
            "status": "publish",
        }
        if slug:
            payload["slug"] = slug
        if article.get("excerpt"):
            payload["excerpt"] = article["excerpt"]
        if featured_media_id is not None:
            payload["featured_media"] = featured_media_id

        meta = self._seo_meta(article, credential)
        if meta:
            payload["meta"] = meta

        response = client.post(f"{base_url}/wp-json/wp/v2/posts", auth=auth, json=payload)
        _raise_for_wp_error(response, "creating the post")
        return response.json()

    def _seo_meta(self, article: dict, credential: WordPressCredential) -> dict[str, str]:
        if credential.seo_plugin == "none":
            return {}
        keys = _SEO_META_KEYS[credential.seo_plugin]
        meta: dict[str, str] = {}
        if article.get("meta_title"):
            meta[keys["title"]] = article["meta_title"]
        if article.get("meta_description"):
            meta[keys["description"]] = article["meta_description"]
        # The JSON-LD the GEO agent built. WordPress has no native REST field
        # for structured data; a theme reads this meta key to print it in
        # <head> (documented, not automatic — see docs/publishing.md).
        if article.get("schema_org"):
            import json

            meta["ai_marketing_schema_org"] = json.dumps(article["schema_org"], ensure_ascii=False)
        return meta


def _raise_for_wp_error(response: httpx.Response, doing: str) -> None:
    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("message", response.text)
        except ValueError:
            message = response.text
        raise ConnectorError(
            f"WordPress refused while {doing} (HTTP {response.status_code}): {message}"
        )
