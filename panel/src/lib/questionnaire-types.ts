/**
 * The questionnaire catalogue and draft state travel over the wire as plain
 * `dict[str, Any]` (see `QuestionnaireState` in `app/schemas/questionnaire.py`)
 * because the backend's Pydantic model deliberately doesn't pin their shape
 * — the catalogue is Python data (`app/services/questionnaire.py`), not a
 * model. These mirror `Question.as_dict()` and `completeness()` there; keep
 * them in sync by hand if that module's shape changes.
 */

export type Localized = Partial<Record<"fa" | "en" | "ar", string>>;

export type QuestionType = "text" | "url" | "textarea" | "list" | "choice" | "multi_choice" | "pairs";

export interface QuestionOption {
  value: string;
  label: Localized;
}

export interface Question {
  id: string;
  section: string;
  type: QuestionType;
  label: Localized;
  help: Localized;
  required: boolean;
  guessable: boolean;
  options: QuestionOption[];
  depends_on: { question: string; value: string } | null;
  max_items: number | null;
}

export interface QuestionnaireSection {
  id: string;
  title: Localized;
  description: Localized;
  questions: Question[];
}

export interface Catalogue {
  sections: QuestionnaireSection[];
}

export interface Completeness {
  answered: number;
  visible: number;
  missing_required: string[];
  can_submit: boolean;
}

export type AnswerValue = string | string[] | { label: string; value: string }[] | undefined;

export type Answers = Record<string, AnswerValue>;

/** Mirrors `app.services.questionnaire.is_visible`. */
export function isVisible(question: Question, answers: Answers): boolean {
  if (!question.depends_on) return true;
  const given = answers[question.depends_on.question];
  if (Array.isArray(given)) {
    return given.some((item) => (typeof item === "string" ? item : item.value) === question.depends_on!.value);
  }
  return given === question.depends_on.value;
}

const EMPTY = new Set(["", null, undefined]);

export function isAnswered(value: AnswerValue): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") return !EMPTY.has(value);
  if (Array.isArray(value)) return value.length > 0;
  return true;
}
