# Language versions: one topic, several locales

Handoff section 11 (phase 2): "نسخه‌های زبانی فرزند یک بسته" — a content
package can have language children, sharing the same underlying research
but written independently in each locale, plus hreflang tags linking the
published versions to each other.

```
POST /packages/{package_id}/language-children  {locale, title}
  └─ app.services.packages.create_language_child()
       ├─ resolves the target to the family's true root first
       ├─ copies the root's latest succeeded RESEARCHER StepRun onto the child
       ├─ ContentPackage(parent_package_id=root.id, locale=..., status=PLANNED)
       └─ rewind_to(child, STRATEGIST)   pipeline continues normally from there
```

## Why the child shares research instead of re-running it

Competitor findings, sourced claims and audience questions do not change
with the output language — only the writing, GEO answer blocks and SEO
metadata do. Re-running the researcher agent per locale would burn tokens
and GPU time on an identical answer. Instead, the parent's most recent
`RESEARCHER` `StepRun` (it must have `status=SUCCEEDED`; a package that
has not reached gate 1 yet has no language children) is copied byte-for-byte
onto the new child, and `rewind_to(child, PipelineStep.STRATEGIST)` — the
same function `POST /packages/{id}/steps/{step}/rerun` already uses — sets
`current_step` so the pipeline's own `next_text_step()` picks up at
STRATEGIST. No new pipeline branch was needed; every agent from STRATEGIST
onward already writes in the package's own locale (`app_prompt`'s system
prompt is parametrised per locale — decision D3, "a package is written in
its own language, not translated").

## A family is always flat

One root, N children — never a child of a child. `create_language_child`
resolves whatever package it is called against to its true root first
(`if parent.parent_package_id is not None: parent = root`), so calling the
endpoint from an already-existing child's own page still adds a flat
sibling rather than nesting a grandchild. `language_siblings(session, pkg)`
relies on this: it takes `root_id = package.parent_package_id or
package.id` and returns every package that is that root or a child of it,
excluding `package` itself. `tests/test_packages.py`'s
`test_a_language_family_stays_flat_not_nested` locks this in.

Other refusals `create_language_child` enforces:
- the locale must be one of `workspace.locales` (an operator/editor
  configures this per workspace; `PATCH /workspaces/{id}` with a `locales`
  list);
- a child cannot use the same locale as its parent;
- a parent cannot get two children in the same locale (checked against
  existing `parent_package_id`/`locale` pairs).

## hreflang: real, but plugin-agnostic

Handoff section 11 also asks for hreflang tags. `PublishContent` gained an
`hreflang_alternates: dict[str, str]` field, populated only for
`Channel.WORDPRESS` publishes
(`app.services.publishing._hreflang_alternates()`): it looks up the
package's `language_siblings()`, then every `Publication` for those
siblings that is `channel=WORDPRESS`, `status=PUBLISHED` and has an
`external_url` — i.e. only siblings that are actually live get an alternate
link; an unpublished sibling is silently left out rather than linking to
nothing.

`app.connectors.wordpress._create_post()` writes that map to a custom post
meta key, `ai_marketing_hreflang` (JSON), the same pattern the GEO agent's
`schema_org` output already uses (`ai_marketing_schema_org`) — not a native
WordPress REST field, since hreflang support depends entirely on which (if
any) multilingual plugin a given site runs (WPML, Polylang, or none).
Rather than guess at one plugin's specific API against no real site to test
it on, the platform writes a plugin-agnostic meta key any theme can read;
printing the actual `<link rel="alternate" hreflang="...">` tags into
`<head>` needs a small theme snippet reading that key, same as the
`schema_org` case — not automatic, and out of scope here.

## Panel

The package detail page shows a language-versions panel: every sibling
links to its own package page, and (while the workspace has a locale not
yet used by this family) a form to add another language child. Adding one
always calls the endpoint against the *currently viewed* package — nesting
correctness is guaranteed server-side, so the panel never needs to resolve
the root itself.
