# Publishing: WordPress and Telegram

Handoff section 10 (phase 1) and section 3, step 6 (three attempts, then an
operator is alerted). Both connectors are real, working HTTP clients — not
simulated — tested against `httpx.MockTransport` fixtures that mirror the
documented WordPress REST API and Telegram Bot API responses. Unlike the
language and image models, there is no phase-0 weight-measurement blocker
here: these are fixed, published HTTP contracts, so the code that speaks
them can be built and verified today.

```
schedule_publication()               a selected variant + a time
  └─ Publication(status=scheduled)
       └─ publish.dispatch (beat, 60s)   finds what is due
            └─ publish.run(publication_id)
                 └─ publishing.attempt()
                      ├─ decrypt the credential
                      ├─ gather_content()      article + selected images, once
                      ├─ build_connector()      WordPress or Telegram
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
Passwords*, revocable on its own) and `TelegramCredential` (a bot token, the
`chat_id` it posts to). Both are pydantic models with `extra="forbid"`, so a
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

**hreflang via WPML or Polylang**, named in the same line of the handoff, is
plugin-specific enough to need a real site to build correctly against — not
attempted yet.

**Telegram** (`app/connectors/telegram.py`):
- `sendPhoto` with a caption when a media asset is selected, `sendMessage`
  otherwise;
- composes the text from the variant's hook, body, hashtags and
  call-to-action, in that order;
- truncates gracefully to Telegram's real limits (1024 chars for a caption,
  4096 for a message) rather than letting the API reject an overlong post —
  `PublishResult.details["truncated"]` says whether it happened.

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

## Reading a selected image without a second HTTP hop

`gather_content()` reads each selected `MediaAsset`'s bytes directly from
`app.services.storage` rather than handing a connector a URL to fetch. The
platform already has the bytes (it is what generated and stored them), so
this avoids a presigned URL expiring mid-publish and means a connector never
depends on the platform's own storage being reachable from wherever the
request happens to run.

## Running without live credentials

Every connector test exercises the real client against `httpx.MockTransport`
handlers shaped like the documented WordPress and Telegram responses — no
simulated stand-in exists or is needed, unlike the LLM and image backends.
`tests/test_publishing_e2e.py` drives the full loop — schedule, sweep,
attempt, retry, operator alert — the same way; only the outbound HTTP is
mocked.
