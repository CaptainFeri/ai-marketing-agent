"""Turning an agent's markdown section body into WordPress-safe HTML.

The writer, GEO and SEO agents all produce ``Section.body`` as plain
markdown (handoff agent contracts). WordPress's REST API takes HTML for
``content``, so this is the one, shared conversion every connector that
posts an article goes through — kept in its own module so the choice of
renderer and its safety settings live in exactly one place.
"""

from __future__ import annotations

import markdown as _markdown

#: Deliberately small. No raw HTML passthrough (``extra`` half-enables it via
#: ``markdown_in_html``, which is not needed here and is one more thing that
#: could carry something unwanted into a customer's site), no footnotes or
#: tables the agents were never asked to produce. Anything the agents did not
#: write should not silently become renderable HTML.
_EXTENSIONS = ["fenced_code", "sane_lists"]


def render(text: str) -> str:
    """Markdown to HTML, safe to hand to WordPress as post content."""
    if not text.strip():
        return ""
    return _markdown.markdown(text, extensions=_EXTENSIONS, output_format="html5")


def render_sections(sections: list[dict]) -> str:
    """A list of ``{heading, level, body}`` sections to one HTML document."""
    parts: list[str] = []
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        if heading:
            level = section.get("level") or 2
            level = level if isinstance(level, int) and 2 <= level <= 6 else 2
            parts.append(f"<h{level}>{_escape(heading)}</h{level}>")
        body = str(section.get("body") or "")
        if body.strip():
            parts.append(render(body))
    return "\n".join(parts)


def render_answer_blocks(blocks: list[dict], title: str) -> str:
    """The GEO agent's answer blocks, appended as an FAQ-style section.

    Handoff section 5's whole point for GEO content: a self-contained
    question/answer pair a generative search engine can quote directly.
    Rendered as ordinary HTML with no special markup — the same reasoning
    the GEO agent itself follows (the block has to work with no ceremony
    around it).
    """
    if not blocks:
        return ""
    items = []
    for block in blocks:
        question = str(block.get("question") or "").strip()
        answer = str(block.get("answer") or "").strip()
        if question and answer:
            items.append(f"<h3>{_escape(question)}</h3>\n<p>{_escape(answer)}</p>")
    if not items:
        return ""
    return f"<h2>{_escape(title)}</h2>\n" + "\n".join(items)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
