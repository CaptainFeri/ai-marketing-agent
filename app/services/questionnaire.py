"""The brand questionnaire (handoff phase 1, week 3).

About twenty-five questions that produce a ``BrandBriefData``. The catalogue
lives here rather than in the panel so that the questions, their validation and
the mapping into the brief cannot drift apart — and so the "guess it" agent is
answering the same questions the customer sees.

Labels are carried in all three supported languages (decision D3). The panel
picks one; it does not translate.

One rule runs through the whole thing: **the assistant may suggest content, but
never a business decision.** Which markets to enter, which channels to publish
on, whether to put a real person's face in a video — those are answers only the
customer can give, so they are marked ``guessable=False`` and the agent is not
shown them as blanks to fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import AppError
from app.db.enums import Channel, VideoMode
from app.schemas.brief import (
    BrandBriefData,
    BrandVoice,
    Competitor,
    Persona,
    Pillar,
    SeedKeywords,
    VisualIdentity,
)

Localized = dict[str, str]


class QuestionType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    URL = "url"
    #: A list of short strings; the panel renders add/remove rows.
    LIST = "list"
    CHOICE = "choice"
    MULTI_CHOICE = "multi_choice"
    #: A list of label/value rows — competitors, goals.
    PAIRS = "pairs"


class QuestionnaireError(AppError):
    """An answer set that cannot become a brief."""

    status_code = 422
    code = "questionnaire_invalid"


@dataclass(frozen=True)
class Option:
    value: str
    label: Localized


@dataclass(frozen=True)
class Question:
    id: str
    section: str
    type: QuestionType
    label: Localized
    help: Localized = field(default_factory=dict)
    required: bool = False
    #: Whether the brief assistant is allowed to propose an answer.
    guessable: bool = True
    options: tuple[Option, ...] = ()
    #: ``(question_id, value)`` — shown only when that answer contains value.
    depends_on: tuple[str, str] | None = None
    max_items: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "type": self.type.value,
            "label": self.label,
            "help": self.help,
            "required": self.required,
            "guessable": self.guessable,
            "options": [{"value": o.value, "label": o.label} for o in self.options],
            "depends_on": (
                {"question": self.depends_on[0], "value": self.depends_on[1]}
                if self.depends_on
                else None
            ),
            "max_items": self.max_items,
        }


@dataclass(frozen=True)
class Section:
    id: str
    title: Localized
    description: Localized


SECTIONS: tuple[Section, ...] = (
    Section(
        "brand",
        {"fa": "برند", "en": "Brand", "ar": "العلامة التجارية"},
        {
            "fa": "شما چه کسی هستید و چه می‌فروشید.",
            "en": "Who you are and what you sell.",
            "ar": "من أنتم وماذا تبيعون.",
        },
    ),
    Section(
        "audience",
        {"fa": "مخاطب", "en": "Audience", "ar": "الجمهور"},
        {
            "fa": "برای چه کسی می‌نویسیم.",
            "en": "Who we are writing for.",
            "ar": "لمن نكتب.",
        },
    ),
    Section(
        "voice",
        {"fa": "لحن", "en": "Voice", "ar": "نبرة الكتابة"},
        {
            "fa": "چطور حرف می‌زنید.",
            "en": "How you sound.",
            "ar": "كيف تتحدثون.",
        },
    ),
    Section(
        "competition",
        {
            "fa": "رقبا و کلیدواژه",
            "en": "Competition and keywords",
            "ar": "المنافسون والكلمات المفتاحية",
        },
        {
            "fa": "با چه کسی مقایسه می‌شوید و مردم چه جست‌وجو می‌کنند.",
            "en": "Who you are compared against, and what people search for.",
            "ar": "بمن تُقارنون، وما الذي يبحث عنه الناس.",
        },
    ),
    Section(
        "content",
        {"fa": "محتوا", "en": "Content", "ar": "المحتوى"},
        {
            "fa": "درباره چه چیزی و با چه هدفی می‌نویسیم.",
            "en": "What we write about, and to what end.",
            "ar": "عمّ نكتب، ولأي غرض.",
        },
    ),
    Section(
        "channels",
        {"fa": "کانال‌ها و رسانه", "en": "Channels and media", "ar": "القنوات والوسائط"},
        {
            "fa": "کجا منتشر می‌شود و چه شکلی دارد.",
            "en": "Where it goes, and what it looks like.",
            "ar": "أين يُنشر، وكيف يبدو.",
        },
    ),
)

LOCALE_OPTIONS = (
    Option("fa", {"fa": "فارسی", "en": "Persian", "ar": "الفارسية"}),
    Option("en", {"fa": "انگلیسی", "en": "English", "ar": "الإنجليزية"}),
    Option("ar", {"fa": "عربی", "en": "Arabic", "ar": "العربية"}),
)

CHANNEL_LABELS: dict[Channel, Localized] = {
    Channel.WORDPRESS: {"fa": "وردپرس", "en": "WordPress", "ar": "ووردبريس"},
    Channel.TELEGRAM: {"fa": "تلگرام", "en": "Telegram", "ar": "تيليجرام"},
    Channel.INSTAGRAM: {"fa": "اینستاگرام", "en": "Instagram", "ar": "إنستغرام"},
    Channel.LINKEDIN: {"fa": "لینکدین", "en": "LinkedIn", "ar": "لينكدإن"},
    Channel.X: {"fa": "X (توییتر)", "en": "X (Twitter)", "ar": "إكس (تويتر)"},
    Channel.YOUTUBE: {"fa": "یوتیوب", "en": "YouTube", "ar": "يوتيوب"},
    Channel.APARAT: {"fa": "آپارات", "en": "Aparat", "ar": "آبارات"},
}

TONE_OPTIONS = (
    Option("professional", {"fa": "حرفه‌ای", "en": "Professional", "ar": "احترافية"}),
    Option("friendly", {"fa": "صمیمی", "en": "Friendly", "ar": "ودّية"}),
    Option("authoritative", {"fa": "متخصص", "en": "Authoritative", "ar": "موثوقة"}),
    Option("plain", {"fa": "ساده و بی‌تکلف", "en": "Plain-spoken", "ar": "مباشرة"}),
    Option("warm", {"fa": "گرم", "en": "Warm", "ar": "دافئة"}),
    Option("witty", {"fa": "شوخ", "en": "Witty", "ar": "طريفة"}),
    Option("technical", {"fa": "فنی", "en": "Technical", "ar": "تقنية"}),
)

PERSON_OPTIONS = (
    Option("we", {"fa": "ما (اول‌شخص جمع)", "en": "We", "ar": "نحن"}),
    Option("i", {"fa": "من (اول‌شخص مفرد)", "en": "I", "ar": "أنا"}),
    Option("you", {"fa": "شما (دوم‌شخص)", "en": "You", "ar": "أنتم"}),
    Option("neutral", {"fa": "بی‌شخص", "en": "Impersonal", "ar": "محايدة"}),
)

READING_LEVEL_OPTIONS = (
    Option("general", {"fa": "عموم مردم", "en": "General public", "ar": "الجمهور العام"}),
    Option("informed", {"fa": "آشنا با موضوع", "en": "Informed reader", "ar": "قارئ مطّلع"}),
    Option("expert", {"fa": "متخصص", "en": "Expert", "ar": "متخصص"}),
)

EVIDENCE_OPTIONS = (
    Option(
        "strict",
        {
            "fa": "هر آمار باید منبع داشته باشد",
            "en": "Every statistic must carry a source",
            "ar": "كل رقم يجب أن يحمل مصدرًا",
        },
    ),
    Option(
        "cite_external_only",
        {
            "fa": "فقط آمار بیرونی منبع لازم دارد",
            "en": "Only external statistics need a source",
            "ar": "الأرقام الخارجية فقط تحتاج مصدرًا",
        },
    ),
)

VIDEO_MODE_OPTIONS = (
    Option(
        VideoMode.NONE.value,
        {
            "fa": "بدون گفتار — فقط تصویر و متن",
            "en": "No speech — image and text only",
            "ar": "بدون صوت — صورة ونص فقط",
        },
    ),
    Option(
        VideoMode.VOICE.value,
        {"fa": "صدای گوینده روی تصویر", "en": "Voice-over", "ar": "تعليق صوتي"},
    ),
    Option(
        VideoMode.FACE.value,
        {
            "fa": "چهره سخنگو (نیاز به رضایت‌نامه کتبی)",
            "en": "Talking head (requires written consent)",
            "ar": "وجه متحدث (يتطلب موافقة خطية)",
        },
    ),
)


def _q(**kwargs: Any) -> Question:
    return Question(**kwargs)


QUESTIONS: tuple[Question, ...] = (
    # ---- brand ----------------------------------------------------------
    _q(
        id="brand_name",
        section="brand",
        type=QuestionType.TEXT,
        required=True,
        guessable=False,
        label={"fa": "نام برند", "en": "Brand name", "ar": "اسم العلامة التجارية"},
    ),
    _q(
        id="website",
        section="brand",
        type=QuestionType.URL,
        guessable=False,
        label={"fa": "آدرس سایت", "en": "Website", "ar": "الموقع الإلكتروني"},
        help={
            "fa": "اگر آدرس را بدهید، دکمه «حدس بزن» از روی سایت شما بقیه را پیشنهاد می‌دهد.",
            "en": "Give an address and the “guess it” button drafts the rest from your site.",
            "ar": "إذا أدخلت العنوان، يقترح زر «خمّن» بقية الإجابات من موقعكم.",
        },
    ),
    _q(
        id="description",
        section="brand",
        type=QuestionType.TEXTAREA,
        required=True,
        label={
            "fa": "برند در یک پاراگراف",
            "en": "The brand in one paragraph",
            "ar": "العلامة في فقرة واحدة",
        },
        help={
            "fa": "چه می‌کنید، برای چه کسی، و چرا کسی باید به شما اهمیت بدهد.",
            "en": "What you do, for whom, and why anyone should care.",
            "ar": "ماذا تفعلون، ولمن، ولماذا يهتم أحد بذلك.",
        },
    ),
    _q(
        id="offerings",
        section="brand",
        type=QuestionType.LIST,
        max_items=15,
        label={
            "fa": "محصولات یا خدمات اصلی",
            "en": "Main products or services",
            "ar": "المنتجات أو الخدمات الرئيسية",
        },
    ),
    _q(
        id="markets",
        section="brand",
        type=QuestionType.MULTI_CHOICE,
        required=True,
        guessable=False,
        options=LOCALE_OPTIONS,
        label={"fa": "بازارها و زبان‌ها", "en": "Markets and languages", "ar": "الأسواق واللغات"},
        help={
            "fa": "برای هر زبان جداگانه کیورد تحقیق می‌شود؛ ترجمه نمی‌کنیم.",
            "en": "Keywords are researched separately per language; nothing is translated.",
            "ar": "تُبحث الكلمات المفتاحية لكل لغة على حدة؛ لا نترجم.",
        },
    ),
    # ---- audience -------------------------------------------------------
    _q(
        id="persona_name",
        section="audience",
        type=QuestionType.TEXT,
        required=True,
        label={
            "fa": "مخاطب اصلی کیست؟",
            "en": "Who is the main reader?",
            "ar": "من هو القارئ الرئيسي؟",
        },
        help={
            "fa": "مثلاً «مدیر خرید کارخانه کوچک» — نه «همه».",
            "en": "For example “purchasing manager at a small factory” — not “everyone”.",
            "ar": "مثلاً «مدير مشتريات في مصنع صغير» — لا «الجميع».",
        },
    ),
    _q(
        id="persona_description",
        section="audience",
        type=QuestionType.TEXTAREA,
        label={"fa": "توضیح مخاطب", "en": "Describe that reader", "ar": "وصف القارئ"},
    ),
    _q(
        id="persona_pains",
        section="audience",
        type=QuestionType.LIST,
        max_items=10,
        label={
            "fa": "چه مشکلی دارند؟",
            "en": "What problems do they have?",
            "ar": "ما المشكلات التي يواجهونها؟",
        },
    ),
    _q(
        id="persona_goals",
        section="audience",
        type=QuestionType.LIST,
        max_items=10,
        label={
            "fa": "به دنبال چه هستند؟",
            "en": "What are they trying to achieve?",
            "ar": "ما الذي يسعون إليه؟",
        },
    ),
    # ---- voice ----------------------------------------------------------
    _q(
        id="tone",
        section="voice",
        type=QuestionType.MULTI_CHOICE,
        required=True,
        options=TONE_OPTIONS,
        label={"fa": "لحن", "en": "Tone", "ar": "النبرة"},
    ),
    _q(
        id="person",
        section="voice",
        type=QuestionType.CHOICE,
        options=PERSON_OPTIONS,
        label={
            "fa": "از چه زاویه‌ای حرف می‌زنید؟",
            "en": "Which voice do you write in?",
            "ar": "بأي ضمير تكتبون؟",
        },
    ),
    _q(
        id="voice_do",
        section="voice",
        type=QuestionType.LIST,
        max_items=10,
        label={"fa": "چه کارهایی بکنیم", "en": "Always do", "ar": "افعلوا دائمًا"},
    ),
    _q(
        id="voice_dont",
        section="voice",
        type=QuestionType.LIST,
        max_items=10,
        label={"fa": "چه کارهایی نکنیم", "en": "Never do", "ar": "لا تفعلوا أبدًا"},
        help={
            "fa": "کلمات ممنوع، ادعاهایی که نباید بکنیم، لحنی که به برند نمی‌خورد.",
            "en": "Banned words, claims you must not make, tones that do not fit.",
            "ar": "كلمات ممنوعة، ادعاءات لا يجوز تقديمها، نبرات لا تناسبكم.",
        },
    ),
    _q(
        id="reading_level",
        section="voice",
        type=QuestionType.CHOICE,
        options=READING_LEVEL_OPTIONS,
        label={"fa": "سطح خواننده", "en": "Reading level", "ar": "مستوى القارئ"},
    ),
    # ---- competition ----------------------------------------------------
    _q(
        id="competitors",
        section="competition",
        type=QuestionType.PAIRS,
        max_items=10,
        label={
            "fa": "رقبا (نام و آدرس)",
            "en": "Competitors (name and URL)",
            "ar": "المنافسون (الاسم والرابط)",
        },
    ),
    _q(
        id="seed_keywords_fa",
        section="competition",
        type=QuestionType.LIST,
        max_items=30,
        depends_on=("markets", "fa"),
        label={
            "fa": "کلیدواژه‌های فارسی",
            "en": "Persian keywords",
            "ar": "كلمات مفتاحية بالفارسية",
        },
    ),
    _q(
        id="seed_keywords_en",
        section="competition",
        type=QuestionType.LIST,
        max_items=30,
        depends_on=("markets", "en"),
        label={
            "fa": "کلیدواژه‌های انگلیسی",
            "en": "English keywords",
            "ar": "كلمات مفتاحية بالإنجليزية",
        },
    ),
    _q(
        id="seed_keywords_ar",
        section="competition",
        type=QuestionType.LIST,
        max_items=30,
        depends_on=("markets", "ar"),
        label={"fa": "کلیدواژه‌های عربی", "en": "Arabic keywords", "ar": "كلمات مفتاحية بالعربية"},
    ),
    # ---- content --------------------------------------------------------
    _q(
        id="pillars",
        section="content",
        type=QuestionType.LIST,
        required=True,
        max_items=10,
        label={"fa": "ستون‌های محتوایی", "en": "Content pillars", "ar": "محاور المحتوى"},
        help={
            "fa": "سه تا پنج موضوع که می‌خواهید برندتان با آن‌ها شناخته شود.",
            "en": "Three to five themes you want to be known for.",
            "ar": "من ثلاثة إلى خمسة موضوعات تريدون أن تُعرفوا بها.",
        },
    ),
    _q(
        id="ai_questions",
        section="content",
        type=QuestionType.LIST,
        max_items=20,
        label={
            "fa": "سؤال‌هایی که می‌خواهید در پاسخ هوش مصنوعی نامتان بیاید",
            "en": "Questions where you want your brand to come up in an AI answer",
            "ar": "أسئلة تريدون أن يُذكر اسمكم في إجابات الذكاء الاصطناعي عنها",
        },
        help={
            "fa": "همان‌طور که یک خریدار می‌پرسد بنویسید. ایجنت پایش GEO دوره‌ای این‌ها را چک می‌کند.",
            "en": "Phrase them as a buyer would. The GEO monitor checks these periodically.",
            "ar": "اكتبوها كما يسأل المشتري. يتحقق منها عميل مراقبة GEO دوريًا.",
        },
    ),
    _q(
        id="goals",
        section="content",
        type=QuestionType.PAIRS,
        max_items=8,
        label={
            "fa": "هدف‌ها (عنوان و مقدار)",
            "en": "Goals (name and target)",
            "ar": "الأهداف (الاسم والقيمة)",
        },
        help={
            "fa": "مثلاً «سرنخ ماهانه» → «۵۰».",
            "en": "For example “monthly leads” → “50”.",
            "ar": "مثلاً «عملاء محتملون شهريًا» ← «٥٠».",
        },
    ),
    _q(
        id="evidence_policy",
        section="content",
        type=QuestionType.CHOICE,
        guessable=False,
        options=EVIDENCE_OPTIONS,
        label={"fa": "قاعده استناد", "en": "Evidence rule", "ar": "قاعدة الاستشهاد"},
        help={
            "fa": "QA بر اساس همین قاعده ادعاهای بی‌منبع را رد می‌کند.",
            "en": "QA rejects unsourced claims against this rule.",
            "ar": "يرفض QA الادعاءات غير الموثقة بناءً على هذه القاعدة.",
        },
    ),
    # ---- channels -------------------------------------------------------
    _q(
        id="channels",
        section="channels",
        type=QuestionType.MULTI_CHOICE,
        required=True,
        guessable=False,
        options=tuple(Option(channel.value, CHANNEL_LABELS[channel]) for channel in Channel),
        label={"fa": "کانال‌های انتشار", "en": "Publishing channels", "ar": "قنوات النشر"},
    ),
    _q(
        id="video_mode",
        section="channels",
        type=QuestionType.CHOICE,
        required=True,
        guessable=False,
        options=VIDEO_MODE_OPTIONS,
        label={
            "fa": "حالت پیش‌فرض ویدیو",
            "en": "Default video mode",
            "ar": "وضع الفيديو الافتراضي",
        },
        help={
            "fa": "برای هر بسته می‌توانید عوضش کنید. حالت چهره بدون رضایت‌نامه ثبت‌شده اجرا نمی‌شود.",
            "en": "Changeable per package. Face mode will not run without a signed consent on file.",
            "ar": "يمكن تغييره لكل حزمة. لا يعمل وضع الوجه دون موافقة موقّعة مسجّلة.",
        },
    ),
    _q(
        id="visual_style",
        section="channels",
        type=QuestionType.LIST,
        max_items=12,
        label={"fa": "سبک تصویری", "en": "Visual style", "ar": "الأسلوب البصري"},
        help={
            "fa": "چند کلمه: مثلاً «مینیمال»، «نور طبیعی»، «صنعتی».",
            "en": "A few words: “minimal”, “natural light”, “industrial”.",
            "ar": "بضع كلمات: «بسيط»، «إضاءة طبيعية»، «صناعي».",
        },
    ),
    _q(
        id="palette",
        section="channels",
        type=QuestionType.LIST,
        max_items=8,
        label={"fa": "رنگ‌های برند", "en": "Brand colours", "ar": "ألوان العلامة"},
        help={
            "fa": "کد هگز، مثل #1F4E79.",
            "en": "Hex codes, e.g. #1F4E79.",
            "ar": "رموز ست عشرية، مثل ‎#1F4E79.",
        },
    ),
)

BY_ID: dict[str, Question] = {question.id: question for question in QUESTIONS}

#: Answers the assistant may propose.
GUESSABLE_IDS: tuple[str, ...] = tuple(q.id for q in QUESTIONS if q.guessable)


def catalogue() -> dict[str, Any]:
    """The whole questionnaire, ready for the panel to render."""
    return {
        "sections": [
            {
                "id": section.id,
                "title": section.title,
                "description": section.description,
                "questions": [q.as_dict() for q in QUESTIONS if q.section == section.id],
            }
            for section in SECTIONS
        ],
        "question_count": len(QUESTIONS),
    }


def is_visible(question: Question, answers: dict[str, Any]) -> bool:
    """False when a conditional question's trigger has not been answered."""
    if question.depends_on is None:
        return True
    source, needed = question.depends_on
    given = answers.get(source)
    if isinstance(given, list):
        return needed in given
    return given == needed


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_pairs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    pairs = []
    for item in value:
        if isinstance(item, dict) and str(item.get("label", "")).strip():
            pairs.append(
                {
                    "label": str(item["label"]).strip(),
                    "value": str(item.get("value", "")).strip(),
                }
            )
    return pairs


def validate_answers(answers: dict[str, Any], *, require_complete: bool = True) -> dict[str, str]:
    """Check an answer set. Returns ``{question_id: problem}``, empty when fine.

    ``require_complete=False`` is used while the wizard is still being filled
    in: unknown ids and bad option values are still reported, but a blank
    required answer is not yet a problem.
    """
    problems: dict[str, str] = {}

    for key in answers:
        if key not in BY_ID:
            problems[key] = "unknown question"

    for question in QUESTIONS:
        if not is_visible(question, answers):
            continue
        value = answers.get(question.id)
        given = value not in (None, "", [], {})

        if require_complete and question.required and not given:
            problems[question.id] = "this answer is required"
            continue
        if not given:
            continue

        if question.options:
            allowed = {option.value for option in question.options}
            chosen = _as_list(value) if question.type is QuestionType.MULTI_CHOICE else [str(value)]
            unknown = [item for item in chosen if item not in allowed]
            if unknown:
                problems[question.id] = f"not an option: {', '.join(sorted(unknown))}"
        if question.max_items and question.type in {QuestionType.LIST, QuestionType.PAIRS}:
            items = _as_list(value) if question.type is QuestionType.LIST else _as_pairs(value)
            if len(items) > question.max_items:
                problems[question.id] = f"at most {question.max_items} entries"

    return problems


# ---------------------------------------------------------------------------
# answers → brief
# ---------------------------------------------------------------------------
def answers_to_brief(answers: dict[str, Any]) -> BrandBriefData:
    """Build the brand brief the agents read.

    Raises :class:`QuestionnaireError` listing every problem at once, so the
    wizard can highlight all of them rather than one per submission.
    """
    problems = validate_answers(answers)
    if problems:
        raise QuestionnaireError(
            "the questionnaire is not complete", details={"problems": problems}
        )

    locales = _as_list(answers.get("markets")) or ["fa"]

    persona = Persona(
        name=str(answers.get("persona_name", "")).strip() or "Primary reader",
        description=str(answers.get("persona_description", "") or "")[:2000],
        pains=_as_list(answers.get("persona_pains")),
        goals=_as_list(answers.get("persona_goals")),
    )

    # Pydantic coerces the string to an HttpUrl, and rejects one that is not
    # a URL — which is the validation we want on a free-text field.
    competitors = [
        Competitor.model_validate({"name": pair["label"], "url": pair["value"] or None})
        for pair in _as_pairs(answers.get("competitors"))
    ]

    evidence = {
        "strict": "every statistic must carry a source URL",
        "cite_external_only": "external statistics must carry a source URL",
    }.get(
        str(answers.get("evidence_policy") or "strict"),
        "every statistic must carry a source URL",
    )

    return BrandBriefData(
        brand=str(answers["brand_name"]).strip(),
        description=str(answers.get("description", "") or "")[:5000],
        website=answers.get("website") or None,
        voice=BrandVoice(
            tone=_as_list(answers.get("tone")),
            person=answers.get("person") or None,
            do=_as_list(answers.get("voice_do")),
            dont=_as_list(answers.get("voice_dont")),
            reading_level=answers.get("reading_level") or None,
        ),
        visual_identity=VisualIdentity(
            palette=_as_list(answers.get("palette")),
            style_keywords=_as_list(answers.get("visual_style")),
        ),
        competitors=competitors,
        personas=[persona],
        seed_keywords=SeedKeywords(
            fa=_as_list(answers.get("seed_keywords_fa")),
            en=_as_list(answers.get("seed_keywords_en")),
            ar=_as_list(answers.get("seed_keywords_ar")),
        ),
        pillars=[Pillar(name=name) for name in _as_list(answers.get("pillars"))],
        ai_questions=_as_list(answers.get("ai_questions")),
        channels=[Channel(value) for value in _as_list(answers.get("channels"))],
        goals={pair["label"]: pair["value"] for pair in _as_pairs(answers.get("goals"))},
        video_mode=VideoMode(str(answers.get("video_mode") or VideoMode.VOICE.value)),
        locales=locales,
        evidence_policy=evidence,
    )


def completeness(answers: dict[str, Any]) -> dict[str, Any]:
    """How far through the wizard the customer is, for the progress bar."""
    visible = [q for q in QUESTIONS if is_visible(q, answers)]
    answered = [q for q in visible if answers.get(q.id) not in (None, "", [], {})]
    missing_required = [
        q.id for q in visible if q.required and answers.get(q.id) in (None, "", [], {})
    ]
    return {
        "answered": len(answered),
        "visible": len(visible),
        "missing_required": missing_required,
        "can_submit": not missing_required,
    }
