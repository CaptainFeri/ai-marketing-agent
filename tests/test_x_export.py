"""X's manual-publish thread export — no live API, purely the text-packing
logic that turns one variant's content into one or several tweets, never
cutting a sentence across two."""

from __future__ import annotations

from app.connectors.base import MediaForPublish, PublishContent
from app.services.x_export import TWEET_LIMIT, build_export, compose_thread


def content(**overrides) -> PublishContent:
    defaults = {
        "title": "راهنمای خرید دریل برقی",
        "body": "متن کوتاه پست.",
        "hook": "قلاب جذاب",
        "hashtags": ("ابزار", "دریل"),
        "call_to_action": "همین حالا بخوانید",
        "article": None,
        "media": (),
        "utm": {},
    }
    defaults.update(overrides)
    return PublishContent(**defaults)


def test_short_content_is_a_single_tweet_with_no_suffix() -> None:
    tweets = compose_thread(content())
    assert len(tweets) == 1
    assert "(1/1)" not in tweets[0]
    assert "قلاب جذاب" in tweets[0]
    assert "متن کوتاه پست." in tweets[0]
    assert "#ابزار" in tweets[0]
    assert "همین حالا بخوانید" in tweets[0]


def test_every_tweet_stays_within_the_real_limit() -> None:
    long_body = "\n\n".join(
        f"این پاراگراف شماره {i} است و کمی طولانی‌تر نوشته شده." for i in range(30)
    )
    tweets = compose_thread(content(body=long_body))
    assert len(tweets) > 1
    for tweet in tweets:
        assert len(tweet) <= TWEET_LIMIT


def test_a_thread_numbers_every_tweet_consistently() -> None:
    long_body = "\n\n".join(f"پاراگراف {i}: " + ("متن " * 20) for i in range(10))
    tweets = compose_thread(content(body=long_body))
    total = len(tweets)
    assert total > 1
    for index, tweet in enumerate(tweets, start=1):
        assert tweet.endswith(f"({index}/{total})")


def test_a_paragraph_is_never_split_when_it_fits_with_the_next_one() -> None:
    """Two short paragraphs that together fit under budget end up in the
    same tweet rather than each getting their own."""
    tweets = compose_thread(content(body="اول.\n\nدوم."))
    assert len(tweets) == 1
    assert "اول." in tweets[0]
    assert "دوم." in tweets[0]


def test_a_long_paragraph_is_split_on_sentence_boundaries_not_mid_word() -> None:
    sentence = "این یک جمله متوسط است که کمی طولانی نوشته شده. "
    paragraph = sentence * 20  # long enough to force a split
    tweets = compose_thread(content(hook=None, hashtags=(), call_to_action=None, body=paragraph))
    assert len(tweets) > 1
    # Every tweet's text (before any "(n/m)" suffix) ends where a sentence
    # ends, never mid-word — the last non-space character before the
    # optional suffix is always sentence-ending punctuation.
    for tweet in tweets:
        text = tweet.rsplit(" (", 1)[0] if " (" in tweet and tweet.rstrip().endswith(")") else tweet
        assert text.rstrip()[-1] in ".!?؟"


def test_a_single_sentence_longer_than_the_budget_is_word_wrapped() -> None:
    huge_sentence = "کلمه " * 100  # no sentence-ending punctuation at all
    tweets = compose_thread(
        content(hook=None, hashtags=(), call_to_action=None, body=huge_sentence.strip())
    )
    assert len(tweets) > 1
    for tweet in tweets:
        assert len(tweet) <= TWEET_LIMIT
    # No word was cut in half: every tweet's words, rejoined, appear in the
    # original text.
    rejoined = " ".join(
        tweet.rsplit(" (", 1)[0] if " (" in tweet else tweet for tweet in tweets
    ).replace("  ", " ")
    for word in huge_sentence.split():
        assert word in rejoined


def test_no_content_at_all_returns_one_empty_tweet_rather_than_crashing() -> None:
    tweets = compose_thread(content(hook=None, body="", hashtags=(), call_to_action=None))
    assert tweets == [""]


def test_build_export_carries_the_first_selected_image() -> None:
    media = MediaForPublish(data=b"\x89PNG...", mime_type="image/png", filename="x.png")
    export = build_export(content(media=(media,)))
    assert export.media is media
    assert len(export.tweets) >= 1


def test_build_export_has_no_media_when_none_is_selected() -> None:
    export = build_export(content(media=()))
    assert export.media is None
