"""X (Twitter): no publish connector — a ready-to-post thread instead.

Handoff section 11: "برای X خروجی «بسته آماده انتشار دستی»" — for X,
produce a manual-publish package rather than a live connector. X's posting
API is expensive and rate-limited in a way none of this platform's other
channels are, and the handoff asks for exactly this instead.

What is worth building for real is the one thing X specifically needs that
no other channel does: splitting content that does not fit one post into a
numbered thread, breaking on paragraph and sentence boundaries rather than
truncating or cutting a sentence in half (contrast
``app.connectors.instagram``/``linkedin``, which truncate — losing the tail
of a caption is acceptable there; silently dropping the second half of an
article on X is not, so it becomes another post instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.connectors.base import MediaForPublish, PublishContent

#: X's character limit for an ordinary (non-Premium) post.
TWEET_LIMIT = 280
#: Reserved for a "(N/M)" thread-position suffix on every tweet once a
#: thread has more than one part. 8 chars comfortably covers up to a
#: 99-tweet thread (" (99/99)" is 8) — nothing realistic here is longer.
_SUFFIX_RESERVE = 8

#: Splits after a sentence-ending mark (Latin or Persian/Arabic) followed by
#: whitespace, without consuming the mark itself.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟])\s+")


@dataclass(frozen=True)
class XExport:
    tweets: tuple[str, ...]
    media: MediaForPublish | None


def _paragraphs(content: PublishContent) -> list[str]:
    parts: list[str] = []
    if content.hook:
        parts.append(content.hook.strip())
    if content.body:
        # The variant's own body may already be multi-paragraph.
        parts.extend(p.strip() for p in content.body.split("\n\n") if p.strip())
    if content.hashtags:
        parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags))
    if content.call_to_action:
        parts.append(content.call_to_action.strip())
    return [p for p in parts if p]


def _wrap_words(text: str, budget: int) -> list[str]:
    """Last resort for a single sentence still longer than the budget:
    break on whitespace, never mid-word."""
    words = text.split(" ")
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip() if line else word
        if len(candidate) <= budget:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _split_long_paragraph(paragraph: str, budget: int) -> list[str]:
    """A paragraph that alone exceeds the budget: break on sentences first,
    falling back to a word wrap for any sentence still too long alone."""
    sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= budget:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(sentence) <= budget:
            current = sentence
        else:
            *complete, current = _wrap_words(sentence, budget) or [""]
            chunks.extend(complete)
    if current:
        chunks.append(current)
    return chunks


def _pack(paragraphs: list[str], budget: int) -> list[str]:
    tweets: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = (
            [paragraph] if len(paragraph) <= budget else _split_long_paragraph(paragraph, budget)
        )
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= budget:
                current = candidate
            else:
                if current:
                    tweets.append(current)
                current = piece
    if current:
        tweets.append(current)
    return tweets


def compose_thread(content: PublishContent) -> list[str]:
    """The variant's text, packed into as few tweets as fit without cutting
    a sentence across two — a real thread when it does not fit in one."""
    paragraphs = _paragraphs(content)
    if not paragraphs:
        return [""]

    # A first pass at the full limit decides whether this is a thread at
    # all; only a real thread needs the "(N/M)" suffix budget reserved.
    if len(_pack(paragraphs, TWEET_LIMIT)) <= 1:
        return _pack(paragraphs, TWEET_LIMIT)

    packed = _pack(paragraphs, TWEET_LIMIT - _SUFFIX_RESERVE)
    total = len(packed)
    return [f"{tweet} ({index}/{total})" for index, tweet in enumerate(packed, start=1)]


def build_export(content: PublishContent) -> XExport:
    return XExport(
        tweets=tuple(compose_thread(content)), media=content.media[0] if content.media else None
    )


__all__ = ["TWEET_LIMIT", "XExport", "build_export", "compose_thread"]
