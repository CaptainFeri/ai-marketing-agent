"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import { useLocale } from "@/lib/locale-context";
import type { Answers, Catalogue, Completeness } from "@/lib/questionnaire-types";
import { isVisible } from "@/lib/questionnaire-types";
import { QuestionField } from "@/components/question-field";
import { Button, Card, ErrorText, Input, Spinner } from "@/components/ui";

const POLL_MS = 3000;

export default function QuestionnairePage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { t, locale } = useLocale();
  const router = useRouter();

  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [answers, setAnswers] = useState<Answers>({});
  const [suggestion, setSuggestion] = useState<Answers>({});
  const [suggestionStatus, setSuggestionStatus] = useState<string>("idle");
  const [completeness, setCompleteness] = useState<Completeness | null>(null);
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const applyState = useCallback((state: {
    catalogue?: unknown;
    answers: Record<string, unknown>;
    suggestion: Record<string, unknown> | null;
    suggestion_status: string;
    completeness: unknown;
    website_url: string | null;
  }) => {
    if (state.catalogue) setCatalogue(state.catalogue as Catalogue);
    setAnswers(state.answers as Answers);
    setSuggestion((state.suggestion as Answers) ?? {});
    setSuggestionStatus(state.suggestion_status);
    setCompleteness(state.completeness as Completeness);
    setWebsiteUrl((prev) => state.website_url ?? prev);
  }, []);

  const load = useCallback(async () => {
    try {
      const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/questionnaire", {
        params: { path: { workspace_id: workspaceId } },
      });
      applyState(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }, [workspaceId, applyState, t]);

  useEffect(() => {
    // Fetch-on-mount: `load` is reused by the polling effect below, so it
    // stays a named callback rather than being inlined into this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    if (suggestionStatus === "running" && !pollRef.current) {
      pollRef.current = setInterval(() => void load(), POLL_MS);
    }
    if (suggestionStatus !== "running" && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [suggestionStatus, load]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const result = await apiClient.PUT("/api/v1/workspaces/{workspace_id}/questionnaire", {
        params: { path: { workspace_id: workspaceId } },
        body: { answers },
      });
      applyState(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  async function guess() {
    setError(null);
    try {
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/questionnaire/guess", {
        params: { path: { workspace_id: workspaceId } },
        body: { website_url: websiteUrl || null, refresh_website: false },
      });
      applyState(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  async function acceptSuggestions(questionIds: string[]) {
    if (questionIds.length === 0) return;
    setError(null);
    try {
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/questionnaire/accept", {
        params: { path: { workspace_id: workspaceId } },
        body: { question_ids: questionIds },
      });
      applyState(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  async function submit() {
    setError(null);
    try {
      await save();
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/questionnaire/submit", {
        params: { path: { workspace_id: workspaceId } },
      });
      const brief = unwrap(result);
      setSubmitted(t("questionnaire.submitted"));
      void brief;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  if (!catalogue || !completeness) {
    return (
      <div className="flex justify-center">
        <Spinner />
      </div>
    );
  }

  const allGuessableIds = catalogue.sections.flatMap((section) =>
    section.questions.filter((q) => q.guessable && suggestion[q.id] !== undefined).map((q) => q.id),
  );

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">{t("questionnaire.title")}</h1>
        <div className="text-sm text-zinc-500">
          {completeness.answered}/{completeness.visible}
        </div>
      </div>

      <Card className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          placeholder={t("questionnaire.website_placeholder")}
          value={websiteUrl}
          onChange={(event) => setWebsiteUrl(event.target.value)}
          className="max-w-xs"
        />
        <Button type="button" onClick={guess} disabled={suggestionStatus === "running"}>
          {suggestionStatus === "running" ? <Spinner /> : null}
          {t("questionnaire.guess")}
        </Button>
        {suggestionStatus === "running" ? <span className="text-sm text-zinc-500">{t("questionnaire.guessing")}</span> : null}
        {suggestionStatus === "ready" && allGuessableIds.length > 0 ? (
          <>
            <span className="text-sm text-green-600">{t("questionnaire.guess_ready")}</span>
            <Button type="button" variant="secondary" onClick={() => acceptSuggestions(allGuessableIds)}>
              {t("questionnaire.accept_all")}
            </Button>
          </>
        ) : null}
        {suggestionStatus === "failed" ? <ErrorText>{t("questionnaire.guess_failed")}</ErrorText> : null}
      </Card>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {submitted ? <p className="mb-4 text-sm text-green-600">{submitted}</p> : null}

      <div className="flex flex-col gap-6">
        {catalogue.sections.map((section) => {
          const visibleQuestions = section.questions.filter((q) => isVisible(q, answers));
          if (visibleQuestions.length === 0) return null;
          return (
            <Card key={section.id}>
              <h2 className="mb-1 font-medium">
                {section.title[locale] ?? section.title.en ?? section.id}
              </h2>
              {section.description[locale] ? (
                <p className="mb-3 text-sm text-zinc-500">{section.description[locale]}</p>
              ) : null}
              <div className="flex flex-col gap-4">
                {visibleQuestions.map((question) => (
                  <QuestionField
                    key={question.id}
                    question={question}
                    value={answers[question.id]}
                    suggestionValue={suggestion[question.id]}
                    onChange={(value) => setAnswers((prev) => ({ ...prev, [question.id]: value }))}
                    onAcceptSuggestion={() => acceptSuggestions([question.id])}
                  />
                ))}
              </div>
            </Card>
          );
        })}
      </div>

      {completeness.missing_required.length > 0 ? (
        <p className="mt-4 text-sm text-amber-600">
          {t("questionnaire.required")}: {completeness.missing_required.join(", ")}
        </p>
      ) : null}

      <div className="mt-4 flex gap-3">
        <Button type="button" variant="secondary" onClick={save} disabled={saving}>
          {saving ? <Spinner /> : null}
          {t("questionnaire.save")}
        </Button>
        <Button type="button" onClick={submit} disabled={!completeness.can_submit}>
          {t("questionnaire.submit")}
        </Button>
        <Button type="button" variant="secondary" onClick={() => router.push(`/workspaces/${workspaceId}/packages`)}>
          {t("nav.packages")}
        </Button>
      </div>
    </div>
  );
}
