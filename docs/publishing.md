# Publishing: WordPress, Telegram, Instagram and LinkedIn

Handoff section 10 (phase 1), section 11 (phase 2: Instagram/LinkedIn), and
section 3, step 6 (three attempts, then an operator is alerted). Every
connector is a real, working HTTP client — not simulated — tested against
`httpx.MockTransport` fixtures that mirror each platform's documented
responses. Unlike the language and image models, there is no phase-0
weight-measurement blocker here: these are fixed, published HTTP contracts,
so the code that speaks them can be built and verified today. X has no
connector — the handoff wants a manual-publish export instead, see
`docs/x-export.md`.

```
schedule_publication()               a selected variant + a time
  └─ Publication(status=scheduled)
       └─ publish.dispatch (beat, 60s)   finds what is due
            └─ publish.run(publication_id)
                 └─ publishing.attempt()
                      ├─ decrypt the credential
                      ├─ gather_content()      article + selected images, once
                      ├─ build_connector()      WordPress, Telegram, Instagram or LinkedIn
                      └─ connector.publish()    real HTTP, real failure modes
```

## Two gates before a connector ever runs

1. `Variant.is_selected` — an editor picks which channel copy goes out.
   `PATCH /packages/{id}/variants/{variant_id}`.
2. `MediaAsset.is_selected` — which generated image (if any) goes with it,
   already required to approve gate 2 (`docs/image-queue.md`).

`schedule_publication()` refuses a variant that has not been selected.
Scheduling is per-channel and can happen one variant at a time as each is
approved — it does not wait for every channel to be ready together.

## Credentials: validated before they are ever encrypted

`app/connectors/credentials.py` defines what each channel needs —
`WordPressCredential` (a site URL, a username, an Application Password — not
the account's login password; created under *Users → Profile → Application
Passwords*, revocable on its own), `TelegramCredential` (a bot token, the
`chat_id` it posts to), `InstagramCredential` (a long-lived access token and
the Instagram Business Account id — not the `@handle`; the operator looks it
up once via `GET /{page-id}?fields=instagram_business_account` in Meta's own
Graph API Explorer) and `LinkedInCredential` (an access token and the
`urn:li:organization:...` it posts as). Every credential is a pydantic model
with `extra="forbid"`, so a
typo'd field is refused at write time rather than discovered on the first
failed publish. `POST /workspaces/{id}/credentials` needs the admin role —
higher than the editor role that runs the rest of the pipeline, since this
is what lets the platform post *as* the customer.

Nothing decrypts a credential except `channel_credentials.decrypt_for_publish`,
called immediately before a connector needs it. `ChannelCredentialOut` (the
API response) only ever carries `public_metadata` — whatever the caller
separately supplied as safe to show, never the encrypted payload.

## What each connector actually does

**WordPress** (`app/connectors/wordpress.py`):
- renders the assembled article's `sections` (markdown, via
  `app/connectors/markdown_render.py`) into the post's HTML content, and
  appends the GEO agent's `answer_blocks` as an FAQ section;
- uploads the first selected image as the featured image, with alt text
  from the SEO agent's `image_alts` (or the package title, as a fallback);
- writes Yoast or Rank Math's meta title/description keys, per
  `WordPressCredential.seo_plugin` — best-effort: neither plugin exposes
  those keys to the REST API by default, and WordPress silently drops meta
  it has no registered permission to write, so sending them never blocks a
  publish. `seo_plugin: "none"` skips the attempt outright;
- stores the GEO agent's `schema_org` JSON-LD under a custom meta key
  (`ai_marketing_schema_org`); surfacing it in `<head>` needs a small theme
  snippet reading that key — not automatic, and out of scope here.
- falls back to the variant's own short body when no article was assembled
  for the package, so a publish never sends an empty post.
- when the package has published language siblings (`docs/language-children.md`),
  writes their locale → URL map under a custom meta key
  (`ai_marketing_hreflang`), the same pattern as `ai_marketing_schema_org`
  above — printing real `<link rel="alternate" hreflang="...">` tags in
  `<head>` needs the same small theme snippet.

**hreflang via WPML or Polylang**, named in the same line of the handoff, is
plugin-specific enough to need a real site to build correctly against —
not attempted. What is built instead is a plugin-agnostic version: the
`ai_marketing_hreflang` meta key above, which any theme can read regardless
of which (if any) multilingual plugin the site runs.

**Telegram** (`app/connectors/telegram.py`):
- `sendPhoto` with a caption when a media asset is selected, `sendMessage`
  otherwise;
- composes the text from the variant's hook, body, hashtags and
  call-to-action, in that order;
- truncates gracefully to Telegram's real limits (1024 chars for a caption,
  4096 for a message) rather than letting the API reject an overlong post —
  `PublishResult.details["truncated"]` says whether it happened.

**Instagram** (`app/connectors/instagram.py`, the Graph API's Content
Publishing flow):
- always needs a selected image — Instagram's feed API has no text-only
  post, so a variant with none is refused here with a clear message rather
  than a confusing one from Meta's API;
- three calls, always in order: create a container from the image's URL,
  poll `GET /{container-id}` until `status_code` is `FINISHED` (up to 60s,
  `_POLL_ATTEMPTS × _POLL_INTERVAL_SECONDS`), then `media_publish`;
- truncates the caption to Instagram's real 2200-character limit.

**LinkedIn** (`app/connectors/linkedin.py`, the current Posts API — not the
deprecated `ugcPosts` one it replaced):
- a selected image, if there is one, is uploaded first
  (`POST /rest/images?action=initializeUpload`, then a `PUT` of the raw
  bytes to the one-time `uploadUrl` that returns) and referenced by URN in
  the post; a text-only post skips this entirely;
- the new post's id comes back in the `x-restli-id` response header, not
  the (empty) JSON body — LinkedIn's own contract, not an oversight here;
- truncates the commentary to a 3000-character limit.

### Instagram and LinkedIn need a publicly reachable image URL

Unlike WordPress and Telegram, both accept only a URL for an image, never a
request body — Meta's and LinkedIn's own servers fetch it. `gather_content()`
now attaches a presigned `StorageBackend.url()` to each selected
`MediaForPublish` for exactly this, and both connectors raise a clear
`ConnectorError` if it is missing rather than guessing at one.

That URL has to actually be reachable from the outside, though: this
platform's default MinIO (`docker-compose.yml`) binds to `127.0.0.1` only.
Publishing to Instagram or LinkedIn in production needs a public bucket or a
CDN in front of storage — an operator-side infrastructure decision this
platform does not make for them.

## The retry state machine lives on the row, not in Celery

Three attempts, then the operator is alerted (handoff section 3, step 6).
That state — `attempt_count`, `last_error`, `operator_alerted` — lives
entirely on `Publication`, the same choice `app.worker.dispatcher` makes for
GPU job retries instead of Celery's own `@task(retry=...)`: a row anyone can
query is easier to test without a broker and easier to show an editor in the
panel than a counter that only exists inside Celery's result backend.

`publishing.attempt()` never raises for an ordinary publish failure — a
`ConnectorError` or a missing credential is written to the row (still
`scheduled` below the limit, `failed` with `operator_alerted=True` at it) and
returned. `publish.dispatch` sweeps `due_publications()` every 60 seconds;
that interval is the retry backoff — there is no second timing mechanism to
reason about. A programming error (an unimplemented channel, a vanished
package) still raises, since retrying that would never succeed.

## Real alerting is not built yet

`operator_alerted=True` and a `logger.error(...)` call are what happens
today. Turning that into an actual notification (email, Slack, whatever the
deployment uses) is a small, separate piece of phase 1/2 work layered on top
of a signal that already exists and is already tested.

## Reading a selected image once, handing connectors both forms

`gather_content()` reads each selected `MediaAsset`'s bytes directly from
`app.services.storage` once, and now also attaches a presigned URL to the
same bytes. WordPress and Telegram both take the raw bytes and never touch
the URL — no dependency on storage being reachable from wherever the
request happens to run, and no presigned URL to expire mid-publish. Instagram
and LinkedIn need the URL instead (see above); each connector reads whichever
form its own API actually wants.

## Running without live credentials

Every connector test exercises the real client against `httpx.MockTransport`
handlers shaped like each platform's documented responses
(`tests/test_connectors.py` for WordPress/Telegram,
`tests/test_instagram_connector.py`, `tests/test_linkedin_connector.py`) —
no simulated stand-in exists or is needed, unlike the LLM and image
backends. `tests/test_publishing_e2e.py` drives the full loop — schedule,
sweep, attempt, retry, operator alert — the same way; only the outbound
HTTP is mocked.
