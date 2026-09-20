"""The brand questionnaire: the catalogue, the mapping, and the wizard flow."""

from __future__ import annotations

import pytest

from app.db.enums import Channel, VideoMode
from app.services import questionnaire
from app.services.questionnaire import (
    BY_ID,
    GUESSABLE_IDS,
    QUESTIONS,
    SECTIONS,
    QuestionnaireError,
    QuestionType,
    answers_to_brief,
    catalogue,
    completeness,
    is_visible,
    validate_answers,
)

LOCALES = ("fa", "en", "ar")

COMPLETE = {
    "brand_name": "آکمه",
    "website": "https://example.com",
    "description": "ابزار برقی برای کارگاه‌های کوچک می‌سازیم.",
    "offerings": ["دریل برقی", "پیچ‌گوشتی شارژی"],
    "markets": ["fa", "en"],
    "persona_name": "مدیر خرید کارگاه",
    "persona_description": "مسئول خرید ابزار در کارگاه‌های زیر ۲۰ نفر.",
    "persona_pains": ["خرابی زودهنگام"],
    "persona_goals": ["کاهش توقف کار"],
    "tone": ["professional", "plain"],
    "person": "we",
    "voice_do": ["مثال واقعی بیاور"],
    "voice_dont": ["ادعای بی‌منبع نکن"],
    "reading_level": "informed",
    "competitors": [{"label": "رقیب الف", "value": "https://competitor.example"}],
    "seed_keywords_fa": ["دریل برقی"],
    "seed_keywords_en": ["power drill"],
    "pillars": ["راهنمای خرید", "نگهداری"],
    "ai_questions": ["بهترین دریل برای کارگاه کوچک چیست؟"],
    "goals": [{"label": "سرنخ ماهانه", "value": "50"}],
    "evidence_policy": "strict",
    "channels": ["wordpress", "telegram"],
    "video_mode": "voice",
    "visual_style": ["مینیمال"],
    "palette": ["#1F4E79"],
}


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------
def test_the_questionnaire_is_about_twenty_five_questions() -> None:
    """The handoff asks for around 25; far more and nobody finishes it."""
    assert 20 <= len(QUESTIONS) <= 30


def test_question_ids_are_unique() -> None:
    assert len({q.id for q in QUESTIONS}) == len(QUESTIONS)


def test_every_question_belongs_to_a_declared_section() -> None:
    sections = {section.id for section in SECTIONS}
    assert {q.section for q in QUESTIONS} <= sections


def test_every_section_has_at_least_one_question() -> None:
    used = {q.section for q in QUESTIONS}
    assert {section.id for section in SECTIONS} == used


@pytest.mark.parametrize("locale", LOCALES)
def test_every_label_exists_in_every_language(locale: str) -> None:
    """Decision D3: the panel picks a language, it does not translate."""
    for question in QUESTIONS:
        assert question.label.get(locale), f"{question.id} has no {locale} label"
        for option in question.options:
            assert option.label.get(locale), f"{question.id}/{option.value} has no {locale}"
    for section in SECTIONS:
        assert section.title.get(locale)
        assert section.description.get(locale)


@pytest.mark.parametrize("locale", LOCALES)
def test_help_text_is_either_complete_or_absent(locale: str) -> None:
    """A half-translated hint is worse than none."""
    for question in QUESTIONS:
        if question.help:
            assert question.help.get(locale), f"{question.id} help missing {locale}"


def test_choice_questions_have_options_and_others_do_not() -> None:
    for question in QUESTIONS:
        if question.type in {QuestionType.CHOICE, QuestionType.MULTI_CHOICE}:
            assert question.options, f"{question.id} has no options"
        else:
            assert not question.options, f"{question.id} should not have options"


def test_conditional_questions_point_at_a_real_trigger() -> None:
    for question in QUESTIONS:
        if question.depends_on:
            source, value = question.depends_on
            assert source in BY_ID, f"{question.id} depends on unknown {source}"
            assert value in {o.value for o in BY_ID[source].options}


def test_the_catalogue_renders_every_question() -> None:
    payload = catalogue()
    rendered = sum(len(section["questions"]) for section in payload["sections"])
    assert rendered == len(QUESTIONS) == payload["question_count"]


def test_business_decisions_are_not_guessable() -> None:
    """The assistant may suggest content, never a decision only the customer can make."""
    for question_id in ("markets", "channels", "video_mode", "brand_name", "website"):
        assert not BY_ID[question_id].guessable, f"{question_id} must not be guessable"


def test_most_content_questions_are_guessable() -> None:
    assert "description" in GUESSABLE_IDS
    assert "pillars" in GUESSABLE_IDS
    assert "tone" in GUESSABLE_IDS


# ---------------------------------------------------------------------------
# conditionals
# ---------------------------------------------------------------------------
def test_arabic_keywords_are_hidden_until_the_market_is_chosen() -> None:
    arabic = BY_ID["seed_keywords_ar"]
    assert not is_visible(arabic, {"markets": ["fa", "en"]})
    assert is_visible(arabic, {"markets": ["fa", "ar"]})


def test_completeness_counts_only_visible_questions() -> None:
    two_markets = completeness({**COMPLETE, "markets": ["fa", "en"]})
    three_markets = completeness({**COMPLETE, "markets": ["fa", "en", "ar"]})
    assert three_markets["visible"] == two_markets["visible"] + 1


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_a_complete_answer_set_has_no_problems() -> None:
    assert validate_answers(COMPLETE) == {}


def test_every_missing_required_question_is_reported_at_once() -> None:
    """The wizard highlights all of them, not one per attempt."""
    problems = validate_answers({"brand_name": "آکمه"})
    assert set(problems) >= {"description", "markets", "pillars", "channels", "video_mode"}


def test_a_partial_save_does_not_complain_about_blanks() -> None:
    assert validate_answers({"brand_name": "آکمه"}, require_complete=False) == {}


def test_an_unknown_question_is_reported_even_on_a_partial_save() -> None:
    problems = validate_answers({"favourite_colour": "blue"}, require_complete=False)
    assert problems["favourite_colour"] == "unknown question"


def test_an_invalid_option_is_rejected() -> None:
    assert "tone" in validate_answers({**COMPLETE, "tone": ["sarcastic"]})
    assert "video_mode" in validate_answers({**COMPLETE, "video_mode": "hologram"})


def test_too_many_entries_are_rejected() -> None:
    problems = validate_answers({**COMPLETE, "pillars": [f"p{i}" for i in range(50)]})
    assert "pillars" in problems


def test_a_hidden_question_is_not_validated() -> None:
    """Arabic keywords cannot be wrong if Arabic was never chosen."""
    answers = {**COMPLETE, "markets": ["fa"], "seed_keywords_ar": ["x"] * 100}
    assert "seed_keywords_ar" not in validate_answers(answers)


# ---------------------------------------------------------------------------
# answers → brief
# ---------------------------------------------------------------------------
def test_a_complete_questionnaire_becomes_a_brief() -> None:
    brief = answers_to_brief(COMPLETE)
    assert brief.brand == "آکمه"
    assert brief.locales == ["fa", "en"]
    assert brief.channels == [Channel.WORDPRESS, Channel.TELEGRAM]
    assert brief.video_mode is VideoMode.VOICE
    assert brief.voice.tone == ["professional", "plain"]
    assert [p.name for p in brief.pillars] == ["راهنمای خرید", "نگهداری"]


def test_keywords_stay_in_their_own_language() -> None:
    """Decision D3: researched per language, never translated across."""
    brief = answers_to_brief(COMPLETE)
    assert brief.seed_keywords.fa == ["دریل برقی"]
    assert brief.seed_keywords.en == ["power drill"]
    assert brief.seed_keywords.ar == []


def test_pairs_become_the_structures_the_agents_read() -> None:
    brief = answers_to_brief(COMPLETE)
    assert brief.competitors[0].name == "رقیب الف"
    assert str(brief.competitors[0].url).startswith("https://competitor.example")
    assert brief.goals == {"سرنخ ماهانه": "50"}


def test_the_evidence_rule_reaches_the_brief() -> None:
    strict = answers_to_brief(COMPLETE)
    assert "every statistic" in strict.evidence_policy

    relaxed = answers_to_brief({**COMPLETE, "evidence_policy": "cite_external_only"})
    assert "external statistics" in relaxed.evidence_policy


def test_an_incomplete_questionnaire_refuses_to_become_a_brief() -> None:
    with pytest.raises(QuestionnaireError) as excinfo:
        answers_to_brief({"brand_name": "آکمه"})
    assert "description" in excinfo.value.details["problems"]


def test_a_competitor_url_that_is_not_a_url_is_rejected() -> None:
    """The URL column is free text in the panel, so it is validated here."""
    from pydantic import ValidationError

    answers = {**COMPLETE, "competitors": [{"label": "x", "value": "not a url"}]}
    with pytest.raises(ValidationError):
        answers_to_brief(answers)


def test_blank_list_entries_are_dropped() -> None:
    brief = answers_to_brief({**COMPLETE, "offerings": ["دریل", "  ", ""]})
    assert brief is not None
    assert questionnaire._as_list(["a", " ", ""]) == ["a"]
