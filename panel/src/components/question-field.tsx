"use client";

import { useLocale } from "@/lib/locale-context";
import type { AnswerValue, Localized, Question } from "@/lib/questionnaire-types";
import { Button, Input, Textarea } from "@/components/ui";

function localized(value: Localized, locale: "fa" | "en" | "ar"): string {
  return value[locale] ?? value.en ?? value.fa ?? Object.values(value)[0] ?? "";
}

interface Props {
  question: Question;
  value: AnswerValue;
  onChange: (value: AnswerValue) => void;
  suggestionValue: AnswerValue;
  onAcceptSuggestion: () => void;
}

export function QuestionField({ question, value, onChange, suggestionValue, onAcceptSuggestion }: Props) {
  const { locale, t } = useLocale();
  const label = localized(question.label, locale);
  const help = localized(question.help, locale);
  const hasSuggestion =
    suggestionValue !== undefined && suggestionValue !== null && suggestionValue !== "" &&
    JSON.stringify(suggestionValue) !== JSON.stringify(value ?? (question.type === "list" || question.type === "pairs" || question.type === "multi_choice" ? [] : ""));

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-sm font-medium">
          {label}
          {question.required ? <span className="ms-1 text-danger">*</span> : null}
        </label>
        {hasSuggestion ? (
          <Button type="button" variant="secondary" className="!px-2 !py-1 text-xs" onClick={onAcceptSuggestion}>
            {t("questionnaire.accept")}
          </Button>
        ) : null}
      </div>
      {help ? <p className="text-xs text-zinc-500">{help}</p> : null}
      <FieldControl question={question} value={value} onChange={onChange} />
    </div>
  );
}

function FieldControl({
  question,
  value,
  onChange,
}: {
  question: Question;
  value: AnswerValue;
  onChange: (value: AnswerValue) => void;
}) {
  const { locale, t } = useLocale();

  if (question.type === "text") {
    return <Input value={(value as string) ?? ""} onChange={(event) => onChange(event.target.value)} />;
  }

  if (question.type === "url") {
    return (
      <Input
        type="url"
        placeholder={t("questionnaire.website_placeholder")}
        value={(value as string) ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  if (question.type === "textarea") {
    return (
      <Textarea rows={4} value={(value as string) ?? ""} onChange={(event) => onChange(event.target.value)} />
    );
  }

  if (question.type === "choice") {
    return (
      <div className="flex flex-wrap gap-2">
        {question.options.map((option) => (
          <label
            key={option.value}
            className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${
              value === option.value ? "border-accent bg-accent text-accent-foreground" : "border-border"
            }`}
          >
            <input
              type="radio"
              name={question.id}
              className="sr-only"
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            {localized(option.label, locale)}
          </label>
        ))}
      </div>
    );
  }

  if (question.type === "multi_choice") {
    const selected = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-2">
        {question.options.map((option) => {
          const checked = selected.includes(option.value);
          return (
            <label
              key={option.value}
              className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${
                checked ? "border-accent bg-accent text-accent-foreground" : "border-border"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={checked}
                onChange={() =>
                  onChange(checked ? selected.filter((v) => v !== option.value) : [...selected, option.value])
                }
              />
              {localized(option.label, locale)}
            </label>
          );
        })}
      </div>
    );
  }

  if (question.type === "list") {
    const items = Array.isArray(value) ? (value as string[]) : [];
    const atMax = question.max_items !== null && items.length >= question.max_items;
    return (
      <div className="flex flex-col gap-2">
        {items.map((item, index) => (
          <div key={index} className="flex gap-2">
            <Input
              value={item}
              onChange={(event) => {
                const next = [...items];
                next[index] = event.target.value;
                onChange(next);
              }}
            />
            <Button
              type="button"
              variant="secondary"
              onClick={() => onChange(items.filter((_, i) => i !== index))}
            >
              {t("common.cancel")}
            </Button>
          </div>
        ))}
        <Button type="button" variant="secondary" disabled={atMax} onClick={() => onChange([...items, ""])}>
          {t("questionnaire.add_item")}
        </Button>
      </div>
    );
  }

  // pairs
  const pairs = Array.isArray(value) ? (value as { label: string; value: string }[]) : [];
  const atMax = question.max_items !== null && pairs.length >= question.max_items;
  return (
    <div className="flex flex-col gap-2">
      {pairs.map((pair, index) => (
        <div key={index} className="flex gap-2">
          <Input
            placeholder={t("questionnaire.pair_label")}
            value={pair.label}
            onChange={(event) => {
              const next = [...pairs];
              next[index] = { ...next[index], label: event.target.value };
              onChange(next);
            }}
          />
          <Input
            placeholder={t("questionnaire.pair_value")}
            value={pair.value}
            onChange={(event) => {
              const next = [...pairs];
              next[index] = { ...next[index], value: event.target.value };
              onChange(next);
            }}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={() => onChange(pairs.filter((_, i) => i !== index))}
          >
            {t("common.cancel")}
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="secondary"
        disabled={atMax}
        onClick={() => onChange([...pairs, { label: "", value: "" }])}
      >
        {t("questionnaire.add_item")}
      </Button>
    </div>
  );
}
