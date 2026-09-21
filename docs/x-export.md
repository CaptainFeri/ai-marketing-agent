# X: a manual-publish thread, not a connector

Handoff section 11 (phase 2): "برای X خروجی «بسته آماده انتشار دستی»" — for
X, produce a ready-to-publish-manually package rather than a live
connector. X's posting API is expensive and rate-limited in a way none of
this platform's other channels are, and the handoff asks for exactly this
instead of building against it.

```
GET /packages/{package_id}/x-export
  ├─ finds the package's selected X variant (Channel.X)
  ├─ app.services.publishing.content_for_variant()   same assembly every
  │                                                    connector gets
  └─ app.services.x_export.build_export()
       └─ splits into a numbered thread, never cutting a sentence in half
```

`app.services.publishing.schedule_publication()` refuses an `X` variant
outright (`InvalidStateError`, pointing here) — scheduling one would only
fail three times against `build_connector(Channel.X)` and alert an
operator over a condition that will never change, since no connector for X
is ever going to exist. The export endpoint is the real, permanent path
instead.

## The thread-splitting logic is the actual work here

Every other connector either accepts arbitrarily long content (WordPress)
or truncates gracefully when it does not fit (Telegram, Instagram,
LinkedIn — see `docs/publishing.md`). Truncating on X would silently drop
the second half of a real article, which is a much bigger loss than
trimming a caption, so `app.services.x_export.compose_thread()` instead:

- packs hook, body paragraphs, hashtags and call-to-action into as few
  280-character tweets as fit, never splitting a paragraph that already
  fits with its neighbor;
- when a paragraph alone is too long, breaks on sentence boundaries
  (`.`/`!`/`?`/`؟`), never mid-sentence;
- when a single sentence is still too long (no punctuation at all), falls
  back to a word wrap — the only thing genuinely undroppable is a whole
  word;
- reserves 8 characters on every tweet in an actual thread for a
  `(N/M)` position suffix, computed from the real resulting thread length,
  not guessed in advance.

Real logic, thoroughly tested (`tests/test_x_export.py`) — not a stub, since
none of it needs a model, a GPU, or an external API to be correct.

## What the operator still does by hand

The panel shows each tweet with a copy button and the selected image, when
the package has a selected X variant (`package.detail`'s `XExportPanel`).
Posting the actual thread — including attaching the image, since there is
no API call to do that here — is a manual step X's own cost and rate
limits made not worth automating for phase 2.
