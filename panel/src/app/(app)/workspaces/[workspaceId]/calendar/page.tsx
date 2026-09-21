"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Badge, Button, Card, ErrorText, Spinner } from "@/components/ui";

type CalendarPublication = components["schemas"]["CalendarPublicationOut"];
type CalendarHoliday = components["schemas"]["CalendarHolidayOut"];
type WorkspaceOut = components["schemas"]["WorkspaceOut"];

// Handoff section 11: "تقویم کشیدن‌ورهاکردنی، منطقه زمانی هر بازار، قوانین
// فاصله بین پست‌ها، هشدار تعطیلات و مناسبت‌ها" — a real month grid, in
// whichever calendar the workspace is configured for, with every
// scheduled/published post as a draggable chip and holidays marked.

//: Extracts a Gregorian date's Jalali (Persian solar calendar) year/month/day
// via ICU's own Persian calendar — the same authority the package detail
// page's own `formatScheduled()` already trusts, rather than a hand-rolled
// conversion.
function jalaliOf(date: Date): { year: number; month: number; day: number } {
  const fmt = new Intl.DateTimeFormat("en-US-u-ca-persian", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    timeZone: "UTC",
  });
  const parts = fmt.formatToParts(date).reduce<Record<string, string>>((acc, p) => {
    acc[p.type] = p.value;
    return acc;
  }, {});
  return {
    year: parseInt(parts.year, 10),
    month: parseInt(parts.month, 10),
    day: parseInt(parts.day, 10),
  };
}

// Every Gregorian UTC date whose Jalali month/year matches, scanning a
// two-year window (cheap: a couple thousand iterations, only on month
// navigation) rather than doing Jalali arithmetic by hand.
function jalaliMonthDays(jalaliYear: number, jalaliMonth: number): Date[] {
  const start = Date.UTC(jalaliYear + 620, 0, 1);
  const end = Date.UTC(jalaliYear + 622, 11, 31);
  const days: Date[] = [];
  for (let t = start; t <= end; t += 86400000) {
    const date = new Date(t);
    const parts = jalaliOf(date);
    if (parts.year === jalaliYear && parts.month === jalaliMonth) days.push(date);
  }
  return days;
}

function gregorianMonthDays(year: number, month: number): Date[] {
  const days: Date[] = [];
  const first = new Date(Date.UTC(year, month - 1, 1));
  for (let t = first.getTime(); ; t += 86400000) {
    const date = new Date(t);
    if (date.getUTCMonth() !== month - 1) break;
    days.push(date);
  }
  return days;
}

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export default function CalendarPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { t, locale } = useLocale();
  const [workspace, setWorkspace] = useState<WorkspaceOut | null>(null);
  const [publications, setPublications] = useState<CalendarPublication[]>([]);
  const [holidays, setHolidays] = useState<CalendarHoliday[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);
  const [dragOverDay, setDragOverDay] = useState<string | null>(null);

  const today = useMemo(() => new Date(), []);
  const [viewYear, setViewYear] = useState<number | null>(null);
  const [viewMonth, setViewMonth] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/api/v1/workspaces/{workspace_id}", { params: { path: { workspace_id: workspaceId } } })
      .then((result) => {
        if (cancelled) return;
        const data = unwrap(result);
        setWorkspace(data);
        if (viewYear === null) {
          const initial = data.calendar === "jalali" ? jalaliOf(today) : {
            year: today.getUTCFullYear(),
            month: today.getUTCMonth() + 1,
          };
          setViewYear(initial.year);
          setViewMonth(initial.month);
        }
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.error")));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const days = useMemo(() => {
    if (!workspace || viewYear === null || viewMonth === null) return [];
    return workspace.calendar === "jalali"
      ? jalaliMonthDays(viewYear, viewMonth)
      : gregorianMonthDays(viewYear, viewMonth);
  }, [workspace, viewYear, viewMonth]);

  const load = useCallback(async () => {
    if (days.length === 0) return;
    try {
      const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/calendar", {
        params: {
          path: { workspace_id: workspaceId },
          query: { start: isoDay(days[0]), end: isoDay(days[days.length - 1]) },
        },
      });
      const data = unwrap(result);
      setPublications(data.publications);
      setHolidays(data.holidays);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, days]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  function goToAdjacentMonth(direction: 1 | -1) {
    if (viewYear === null || viewMonth === null) return;
    let month = viewMonth + direction;
    let year = viewYear;
    if (month > 12) {
      month = 1;
      year += 1;
    } else if (month < 1) {
      month = 12;
      year -= 1;
    }
    setViewYear(year);
    setViewMonth(month);
  }

  function goToToday() {
    if (!workspace) return;
    const initial =
      workspace.calendar === "jalali"
        ? jalaliOf(today)
        : { year: today.getUTCFullYear(), month: today.getUTCMonth() + 1 };
    setViewYear(initial.year);
    setViewMonth(initial.month);
  }

  function dayLabel(date: Date): string {
    return workspace?.calendar === "jalali" ? String(jalaliOf(date).day) : String(date.getUTCDate());
  }

  function monthLabel(): string {
    if (!workspace || viewYear === null || viewMonth === null) return "";
    if (workspace.calendar === "jalali") {
      const monthNames = [
        "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
        "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
      ];
      return `${monthNames[viewMonth - 1]} ${viewYear}`;
    }
    return new Date(Date.UTC(viewYear, viewMonth - 1, 1)).toLocaleDateString(undefined, {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    });
  }

  function timeInWorkspaceZone(iso: string): string {
    if (!workspace) return iso;
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: workspace.timezone,
    }).format(new Date(iso));
  }

  async function handleDrop(day: Date, publicationId: string) {
    setDropError(null);
    const publication = publications.find((p) => p.id === publicationId);
    if (!publication) return;
    // Keeps the same UTC time-of-day, only the calendar date changes -- a
    // full wall-clock-preserving conversion across the workspace's own
    // timezone would also need to account for DST at the drop target,
    // which this does not attempt.
    const original = new Date(publication.scheduled_at);
    const moved = new Date(
      Date.UTC(
        day.getUTCFullYear(),
        day.getUTCMonth(),
        day.getUTCDate(),
        original.getUTCHours(),
        original.getUTCMinutes(),
        original.getUTCSeconds(),
      ),
    );
    try {
      await apiClient.PATCH("/api/v1/packages/{package_id}/publications/{publication_id}", {
        params: {
          path: { package_id: publication.package_id, publication_id: publication.id },
        },
        body: { scheduled_at: moved.toISOString() },
      });
      await load();
    } catch (err) {
      setDropError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  if (!workspace || viewYear === null) {
    return (
      <div className="flex justify-center">
        {error ? <ErrorText>{error}</ErrorText> : <Spinner />}
      </div>
    );
  }

  const holidaysByDay = new Map(holidays.map((h) => [h.date, h]));
  const publicationsByDay = new Map<string, CalendarPublication[]>();
  for (const publication of publications) {
    const key = isoDay(new Date(publication.scheduled_at));
    const list = publicationsByDay.get(key) ?? [];
    list.push(publication);
    publicationsByDay.set(key, list);
  }

  const leadingBlanks =
    workspace.calendar === "jalali"
      ? (new Date(days[0]).getUTCDay() + 1) % 7
      : new Date(days[0]).getUTCDay();

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">{t("calendar.title")}</h1>
          <p className="text-xs text-zinc-500">
            {t("calendar.timezone")}: {workspace.timezone} · {t("calendar.spacing")}:{" "}
            {workspace.min_publish_spacing_minutes} {t("calendar.spacing_minutes")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" onClick={() => goToAdjacentMonth(-1)}>
            {t("calendar.prev")}
          </Button>
          <span className="min-w-32 text-center text-sm font-medium">{monthLabel()}</span>
          <Button type="button" variant="secondary" onClick={() => goToAdjacentMonth(1)}>
            {t("calendar.next")}
          </Button>
          <Button type="button" variant="secondary" onClick={goToToday}>
            {t("calendar.today")}
          </Button>
        </div>
      </div>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {dropError ? <ErrorText>{dropError}</ErrorText> : null}
      <p className="text-xs text-muted-foreground">{t("calendar.drag_hint")}</p>

      <Card>
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: leadingBlanks }).map((_, index) => (
            <div key={`blank-${index}`} />
          ))}
          {days.map((day) => {
            const key = isoDay(day);
            const holiday = holidaysByDay.get(key);
            const dayPublications = publicationsByDay.get(key) ?? [];
            const isDragOver = dragOverDay === key;
            return (
              <div
                key={key}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragOverDay(key);
                }}
                onDragLeave={() => setDragOverDay((current) => (current === key ? null : current))}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragOverDay(null);
                  const publicationId = event.dataTransfer.getData("text/publication-id");
                  if (publicationId) void handleDrop(day, publicationId);
                }}
                className={`flex min-h-24 flex-col gap-1 rounded-md border p-1.5 text-xs ${
                  holiday
                    ? "border-amber-500/40 bg-amber-500/10"
                    : isDragOver
                      ? "border-accent bg-accent/10"
                      : "border-border"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{dayLabel(day)}</span>
                  {holiday ? (
                    <span
                      className="truncate text-[10px] text-amber-700 dark:text-amber-400"
                      title={locale === "fa" ? holiday.name_fa : holiday.name_en}
                    >
                      {locale === "fa" ? holiday.name_fa : holiday.name_en}
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-1 flex-col gap-1">
                  {dayPublications.map((publication) => (
                    <div
                      key={publication.id}
                      draggable={publication.status === "scheduled"}
                      onDragStart={(event) => {
                        event.dataTransfer.setData("text/publication-id", publication.id);
                      }}
                      className={`truncate rounded px-1 py-0.5 ${
                        publication.status === "scheduled"
                          ? "cursor-grab bg-accent/20"
                          : "cursor-default bg-surface"
                      }`}
                      title={`${publication.package_title} · ${publication.channel}`}
                    >
                      <Badge
                        tone={
                          publication.status === "published"
                            ? "good"
                            : publication.status === "failed"
                              ? "bad"
                              : "neutral"
                        }
                      >
                        {publication.channel}
                      </Badge>{" "}
                      {timeInWorkspaceZone(publication.scheduled_at)} · {publication.package_title}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
