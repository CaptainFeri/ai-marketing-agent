"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Badge, Button, Card, ErrorText, Input, Spinner, Textarea } from "@/components/ui";

type PackageDetail = components["schemas"]["PackageDetail"];
// The backend defaults these list fields with `Field(default_factory=list)`,
// which openapi-typescript renders as optional — but `PackageDetailPage`
// always normalizes them to arrays right after the fetch, so the rest of
// this file can treat them as always present.
type NormalizedPackageDetail = PackageDetail & {
  step_runs: NonNullable<PackageDetail["step_runs"]>;
  variants: NonNullable<PackageDetail["variants"]>;
  media_assets: NonNullable<PackageDetail["media_assets"]>;
  language_siblings: NonNullable<PackageDetail["language_siblings"]>;
  ab_test_results: NonNullable<PackageDetail["ab_test_results"]>;
};
type Publication = components["schemas"]["PublicationOut"];
type ApprovalGate = components["schemas"]["ApprovalGate"];
type ApprovalDecision = components["schemas"]["ApprovalDecision"];
type PipelineStep = components["schemas"]["PipelineStep"];

// Handoff section 4: "زمان‌بندی ساده (لیستی با تاریخ شمسی/میلادی)" — the
// publication list reads Jalali or Gregorian depending on the workspace's
// own calendar setting. `Intl`'s Persian calendar does the conversion, so
// no date-math library is needed for it.
function formatScheduled(iso: string, calendar: "jalali" | "gregorian"): string {
  const date = new Date(iso);
  if (calendar === "jalali") {
    return new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }
  return date.toLocaleString();
}

const PIPELINE_STEPS: PipelineStep[] = [
  "researcher",
  "strategist",
  "writer",
  "geo_optimizer",
  "seo_optimizer",
  "qa",
  "marketizer",
];

export default function PackageDetailPage() {
  const { workspaceId, packageId } = useParams<{ workspaceId: string; packageId: string }>();
  const { t } = useLocale();
  const [pkg, setPkg] = useState<NormalizedPackageDetail | null>(null);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [calendar, setCalendar] = useState<"jalali" | "gregorian">("gregorian");
  const [workspaceLocales, setWorkspaceLocales] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const [detail, pubs, workspace] = await Promise.all([
        apiClient.GET("/api/v1/packages/{package_id}", { params: { path: { package_id: packageId } } }),
        apiClient.GET("/api/v1/packages/{package_id}/publications", {
          params: { path: { package_id: packageId } },
        }),
        apiClient.GET("/api/v1/workspaces/{workspace_id}", {
          params: { path: { workspace_id: workspaceId } },
        }),
      ]);
      const data = unwrap(detail);
      setPkg({
        ...data,
        step_runs: data.step_runs ?? [],
        variants: data.variants ?? [],
        media_assets: data.media_assets ?? [],
        language_siblings: data.language_siblings ?? [],
        ab_test_results: data.ab_test_results ?? [],
      });
      setPublications(unwrap(pubs));
      const workspaceData = unwrap(workspace);
      setCalendar(workspaceData.calendar === "jalali" ? "jalali" : "gregorian");
      setWorkspaceLocales(workspaceData.locales);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  useEffect(() => {
    // Fetch-on-mount: `load` is reused by `guarded()` after every mutation
    // below, so it stays a named function rather than being inlined here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, packageId]);

  async function guarded(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function decideGate(gate: ApprovalGate, decision: ApprovalDecision, feedback: string, returnToStep: PipelineStep | "") {
    await guarded(() =>
      apiClient.POST("/api/v1/packages/{package_id}/gates/{gate}", {
        params: { path: { package_id: packageId, gate } },
        body: {
          decision,
          feedback: feedback || null,
          return_to_step: returnToStep || null,
        },
      }),
    );
  }

  async function selectMedia(mediaAssetId: string, isSelected: boolean) {
    await guarded(() =>
      apiClient.PATCH("/api/v1/packages/{package_id}/media/{media_asset_id}", {
        params: { path: { package_id: packageId, media_asset_id: mediaAssetId } },
        body: { is_selected: isSelected },
      }),
    );
  }

  async function selectVariant(variantId: string, isSelected: boolean) {
    await guarded(() =>
      apiClient.PATCH("/api/v1/packages/{package_id}/variants/{variant_id}", {
        params: { path: { package_id: packageId, variant_id: variantId } },
        body: { is_selected: isSelected },
      }),
    );
  }

  async function rerunStep(step: PipelineStep) {
    await guarded(() =>
      apiClient.POST("/api/v1/packages/{package_id}/steps/{step}/rerun", {
        params: { path: { package_id: packageId, step } },
      }),
    );
  }

  if (!pkg) {
    return (
      <div className="flex justify-center">
        {error ? <ErrorText>{error}</ErrorText> : <Spinner />}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">{pkg.title}</h1>
          <p className="text-xs text-zinc-500">
            {pkg.locale} · {pkg.video_mode} · {t("package.gpu_seconds")}: {pkg.gpu_seconds.toFixed(1)}
          </p>
        </div>
        <Badge tone="neutral">{pkg.status}</Badge>
      </div>

      {error ? <ErrorText>{error}</ErrorText> : null}

      <LanguageVersionsPanel
        workspaceId={workspaceId}
        packageId={packageId}
        packageLocale={pkg.locale}
        isChild={pkg.parent_package_id !== null}
        siblings={pkg.language_siblings}
        workspaceLocales={workspaceLocales}
        busy={busy}
        onCreated={load}
      />

      <Card>
        <h2 className="mb-2 font-medium">{t("package.steps")}</h2>
        <div className="flex flex-col gap-1">
          {pkg.step_runs.map((run) => (
            <div key={run.id} className="flex items-center justify-between text-sm">
              <span>{run.step}</span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-500">{run.status}</span>
                {PIPELINE_STEPS.includes(run.step) ? (
                  <Button
                    type="button"
                    variant="secondary"
                    className="!px-2 !py-0.5 text-xs"
                    disabled={busy}
                    onClick={() => rerunStep(run.step)}
                  >
                    {t("package.rerun")}
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {pkg.status === "text_review" ? (
        <GatePanel
          title={t("package.gate1.title")}
          gate="text"
          busy={busy}
          onDecide={decideGate}
          article={pkg.article}
        />
      ) : null}

      {pkg.variants.length > 0 ? (
        <Card>
          <h2 className="mb-2 font-medium">{t("package.variants")}</h2>
          <div className="flex flex-col gap-3">
            {pkg.variants.map((variant) => (
              <div key={variant.id} className="rounded-md border border-border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">
                    {variant.channel} {variant.ab_label ? `(${variant.ab_label})` : ""}
                  </span>
                  <Button
                    type="button"
                    variant={variant.is_selected ? "primary" : "secondary"}
                    className="!px-2 !py-1 text-xs"
                    disabled={busy}
                    onClick={() => selectVariant(variant.id, !variant.is_selected)}
                  >
                    {variant.is_selected ? t("package.selected") : t("package.select")}
                  </Button>
                </div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-zinc-500">
                  {JSON.stringify(variant.body, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {pkg.ab_test_results.length > 0 ? (
        <ABTestResultsPanel results={pkg.ab_test_results} variants={pkg.variants} />
      ) : null}

      {pkg.media_assets.length > 0 ? (
        <Card>
          <h2 className="mb-2 font-medium">{t("package.media")}</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {pkg.media_assets.map((asset) => (
              <div key={asset.id} className="flex flex-col gap-1 rounded-md border border-border p-2">
                {asset.url && asset.mime_type?.startsWith("image/") ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={asset.url} alt={asset.prompt ?? asset.kind} className="aspect-square w-full rounded object-cover" />
                ) : asset.url && asset.mime_type?.startsWith("video/") ? (
                  <video src={asset.url} controls className="aspect-square w-full rounded object-cover" />
                ) : asset.url && asset.mime_type?.startsWith("audio/") ? (
                  <div className="flex aspect-square w-full flex-col items-center justify-center gap-2 rounded bg-surface p-2 text-xs text-zinc-500">
                    {asset.kind}
                    <audio src={asset.url} controls className="w-full" />
                  </div>
                ) : (
                  <div className="flex aspect-square w-full items-center justify-center rounded bg-surface text-xs text-zinc-500">
                    {asset.url ? (
                      <a href={asset.url} target="_blank" rel="noreferrer" className="underline">
                        {asset.kind}
                      </a>
                    ) : (
                      asset.kind
                    )}
                  </div>
                )}
                <Button
                  type="button"
                  variant={asset.is_selected ? "primary" : "secondary"}
                  className="!px-2 !py-1 text-xs"
                  disabled={busy || !asset.mime_type}
                  onClick={() => selectMedia(asset.id, !asset.is_selected)}
                >
                  {asset.is_selected ? t("package.selected") : t("package.select")}
                </Button>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {pkg.status === "selection" ? (
        <GatePanel title={t("package.gate2.title")} gate="media" busy={busy} onDecide={decideGate} />
      ) : null}

      {pkg.variants.some((v) => v.channel === "x" && v.is_selected) ? (
        <XExportPanel packageId={packageId} mediaAssets={pkg.media_assets} />
      ) : null}

      <PublicationsPanel
        packageId={packageId}
        publications={publications}
        variants={pkg.variants}
        calendar={calendar}
        busy={busy}
        onScheduled={load}
        onCancel={(publicationId) =>
          guarded(() =>
            apiClient.POST("/api/v1/packages/{package_id}/publications/{publication_id}/cancel", {
              params: { path: { package_id: packageId, publication_id: publicationId } },
            }),
          )
        }
      />

      {publications.length > 0 ? <MetricsPanel packageId={packageId} /> : null}
    </div>
  );
}

function GatePanel({
  title,
  gate,
  busy,
  onDecide,
  article,
}: {
  title: string;
  gate: ApprovalGate;
  busy: boolean;
  onDecide: (gate: ApprovalGate, decision: ApprovalDecision, feedback: string, returnToStep: PipelineStep | "") => void;
  article?: Record<string, unknown> | null;
}) {
  const { t } = useLocale();
  const [feedback, setFeedback] = useState("");
  const [returnToStep, setReturnToStep] = useState<PipelineStep | "">("");

  return (
    <Card>
      <h2 className="mb-2 font-medium">{title}</h2>
      {article ? (
        <pre className="mb-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-surface p-2 text-xs">
          {JSON.stringify(article, null, 2)}
        </pre>
      ) : null}
      <Textarea
        rows={2}
        placeholder={t("package.feedback")}
        value={feedback}
        onChange={(event) => setFeedback(event.target.value)}
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select
          value={returnToStep}
          onChange={(event) => setReturnToStep(event.target.value as PipelineStep | "")}
          className="rounded-md border border-border bg-transparent px-2 py-1 text-xs"
        >
          <option value="">{t("package.return_to_step")}</option>
          {PIPELINE_STEPS.map((step) => (
            <option key={step} value={step}>
              {step}
            </option>
          ))}
        </select>
        <Button type="button" disabled={busy} onClick={() => onDecide(gate, "approved", feedback, returnToStep)}>
          {t("package.approve")}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={() => onDecide(gate, "changes_requested", feedback, returnToStep)}
        >
          {t("package.request_changes")}
        </Button>
        <Button type="button" variant="danger" disabled={busy} onClick={() => onDecide(gate, "rejected", feedback, returnToStep)}>
          {t("package.reject")}
        </Button>
      </div>
    </Card>
  );
}

function PublicationsPanel({
  packageId,
  publications,
  variants,
  calendar,
  busy,
  onScheduled,
  onCancel,
}: {
  packageId: string;
  publications: Publication[];
  variants: NormalizedPackageDetail["variants"];
  calendar: "jalali" | "gregorian";
  busy: boolean;
  onScheduled: () => Promise<void>;
  onCancel: (publicationId: string) => void;
}) {
  const { t } = useLocale();
  const [variantId, setVariantId] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const selectedVariants = variants.filter((v) => v.is_selected);

  async function schedule() {
    if (!variantId || !scheduledAt) return;
    setError(null);
    try {
      await apiClient.POST("/api/v1/packages/{package_id}/publications", {
        params: { path: { package_id: packageId } },
        body: { variant_id: variantId, scheduled_at: new Date(scheduledAt).toISOString() },
      });
      setVariantId("");
      setScheduledAt("");
      await onScheduled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  return (
    <Card>
      <h2 className="mb-2 font-medium">{t("package.publications")}</h2>
      {selectedVariants.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <select
            value={variantId}
            onChange={(event) => setVariantId(event.target.value)}
            className="rounded-md border border-border bg-transparent px-2 py-2 text-sm"
          >
            <option value="">{t("package.variants")}</option>
            {selectedVariants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.channel} {v.ab_label ?? ""}
              </option>
            ))}
          </select>
          <div className="flex flex-col gap-1">
            <Input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} />
            {calendar === "jalali" && scheduledAt ? (
              <span className="text-xs text-muted-foreground">
                {formatScheduled(new Date(scheduledAt).toISOString(), "jalali")}
              </span>
            ) : null}
          </div>
          <Button type="button" disabled={busy || !variantId || !scheduledAt} onClick={schedule}>
            {t("package.schedule")}
          </Button>
        </div>
      ) : null}
      {error ? <ErrorText>{error}</ErrorText> : null}
      <div className="flex flex-col gap-2">
        {publications.map((pub) => (
          <div key={pub.id} className="flex items-center justify-between rounded-md border border-border p-2 text-sm">
            <span>
              {pub.channel} · {formatScheduled(pub.scheduled_at, calendar)}
            </span>
            <div className="flex items-center gap-2">
              <Badge tone={pub.status === "published" ? "good" : pub.status === "failed" ? "bad" : "neutral"}>
                {pub.status}
              </Badge>
              {pub.status === "scheduled" ? (
                <Button type="button" variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => onCancel(pub.id)}>
                  {t("package.cancel")}
                </Button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

type MetricSnapshot = components["schemas"]["MetricSnapshotOut"];

function MetricsPanel({ packageId }: { packageId: string }) {
  const { t } = useLocale();
  const [snapshots, setSnapshots] = useState<MetricSnapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/api/v1/packages/{package_id}/metrics", { params: { path: { package_id: packageId } } })
      .then((result) => {
        if (!cancelled) setSnapshots(unwrap(result).snapshots);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [packageId, t]);

  return (
    <Card>
      <h2 className="mb-2 font-medium">{t("analytics.metrics_title")}</h2>
      {error ? <ErrorText>{error}</ErrorText> : null}
      {snapshots === null && !error ? <Spinner /> : null}
      {snapshots?.length === 0 ? (
        <p className="text-sm text-zinc-500">{t("analytics.metrics_empty")}</p>
      ) : null}
      {snapshots && snapshots.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-start text-sm">
            <thead>
              <tr className="text-xs text-zinc-500">
                <th className="p-1 text-start">{t("analytics.date")}</th>
                <th className="p-1 text-start">{t("analytics.source")}</th>
                <th className="p-1 text-start">{t("analytics.impressions")}</th>
                <th className="p-1 text-start">{t("analytics.clicks")}</th>
                <th className="p-1 text-start">{t("analytics.position")}</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((snapshot) => (
                <tr key={snapshot.id} className="border-t border-border">
                  <td className="p-1">{new Date(snapshot.captured_for).toLocaleDateString()}</td>
                  <td className="p-1">{snapshot.source}</td>
                  <td className="p-1">{snapshot.impressions ?? "—"}</td>
                  <td className="p-1">{snapshot.clicks ?? "—"}</td>
                  <td className="p-1">{snapshot.position?.toFixed(1) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}

type PackageOut = components["schemas"]["PackageOut"];

// Handoff section 11: a language "family" is flat — every sibling, whether
// this package is the root or a child, links to every other sibling plus
// lets you add another. The server (not this component) resolves the
// family's true root, so this always works correctly no matter which
// sibling's page the "add" form is submitted from.
function LanguageVersionsPanel({
  workspaceId,
  packageId,
  packageLocale,
  isChild,
  siblings,
  workspaceLocales,
  busy,
  onCreated,
}: {
  workspaceId: string;
  packageId: string;
  packageLocale: string;
  isChild: boolean;
  siblings: PackageOut[];
  workspaceLocales: string[];
  busy: boolean;
  onCreated: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [locale, setLocale] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const takenLocales = new Set([packageLocale, ...siblings.map((s) => s.locale)]);
  const availableLocales = workspaceLocales.filter((l) => !takenLocales.has(l));

  async function createChild(event: FormEvent) {
    event.preventDefault();
    if (!locale || !title.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await apiClient.POST("/api/v1/packages/{package_id}/language-children", {
        params: { path: { package_id: packageId } },
        body: { locale, title: title.trim() },
      });
      setLocale("");
      setTitle("");
      await onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setCreating(false);
    }
  }

  return (
    <Card>
      <h2 className="mb-2 font-medium">{t("package.language_versions.title")}</h2>
      {isChild ? (
        <p className="mb-2 text-xs text-muted-foreground">{t("package.language_versions.this_is_a_child")}</p>
      ) : null}
      {siblings.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-2">
          {siblings.map((sibling) => (
            <Link
              key={sibling.id}
              href={`/workspaces/${workspaceId}/packages/${sibling.id}`}
              className="rounded-md border border-border px-2 py-1 text-xs hover:bg-surface"
            >
              {sibling.locale} · {sibling.title}
            </Link>
          ))}
        </div>
      ) : (
        <p className="mb-3 text-xs text-muted-foreground">{t("package.language_versions.empty")}</p>
      )}
      {error ? <ErrorText>{error}</ErrorText> : null}
      {availableLocales.length > 0 ? (
        <form onSubmit={createChild} className="flex flex-wrap items-end gap-2">
          <select
            value={locale}
            onChange={(event) => setLocale(event.target.value)}
            className="rounded-md border border-border bg-transparent px-2 py-2 text-sm"
          >
            <option value="">{t("package.language_versions.locale")}</option>
            {availableLocales.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <Input
            placeholder={t("package.language_versions.new_title")}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <Button type="submit" disabled={busy || creating || !locale || !title.trim()}>
            {t("package.language_versions.create")}
          </Button>
        </form>
      ) : (
        <p className="text-xs text-muted-foreground">{t("package.language_versions.no_locales_left")}</p>
      )}
    </Card>
  );
}

type ABTestResult = components["schemas"]["ABTestResultOut"];

// Handoff section 11: hook A/B results, compared by click-through rate (not
// raw clicks — see app.services.ab_testing's module docstring for why) and
// re-evaluated daily as more metrics arrive, so this always shows the
// current standing rather than a one-time snapshot.
function ABTestResultsPanel({
  results,
  variants,
}: {
  results: ABTestResult[];
  variants: NormalizedPackageDetail["variants"];
}) {
  const { t } = useLocale();

  function hookFor(variantId: string): string {
    const variant = variants.find((v) => v.id === variantId);
    const body = variant?.body as { hook?: string } | undefined;
    return body?.hook ?? variantId;
  }

  function ctr(clicks: number, impressions: number): string {
    return impressions > 0 ? `${((clicks / impressions) * 100).toFixed(1)}%` : "—";
  }

  return (
    <Card>
      <h2 className="mb-2 font-medium">{t("package.ab_results.title")}</h2>
      <div className="flex flex-col gap-3">
        {results.map((result) => (
          <div key={result.id} className="rounded-md border border-border p-3 text-sm">
            <div className="mb-2 text-xs font-medium text-zinc-500">{result.channel}</div>
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["a", result.a_variant_id, result.a_clicks, result.a_impressions],
                  ["b", result.b_variant_id, result.b_clicks, result.b_impressions],
                ] as const
              ).map(([label, variantId, clicks, impressions]) => (
                <div
                  key={label}
                  className={`rounded-md p-2 ${
                    result.winner_variant_id === variantId
                      ? "bg-green-500/10 dark:bg-green-500/15"
                      : "bg-surface"
                  }`}
                >
                  <div className="mb-1 flex items-center gap-1 text-xs font-medium uppercase">
                    {label}
                    {result.winner_variant_id === variantId ? (
                      <Badge tone="good">{t("package.ab_results.winner")}</Badge>
                    ) : null}
                  </div>
                  <p className="truncate text-xs text-zinc-500" title={hookFor(variantId)}>
                    {hookFor(variantId)}
                  </p>
                  <p className="mt-1 text-xs">
                    {ctr(clicks, impressions)} · {clicks}/{impressions}
                  </p>
                </div>
              ))}
            </div>
            {result.winner_variant_id === null ? (
              <p className="mt-2 text-xs text-muted-foreground">{t("package.ab_results.tie")}</p>
            ) : null}
          </div>
        ))}
      </div>
    </Card>
  );
}

type XExport = components["schemas"]["XExportOut"];

function XExportPanel({
  packageId,
  mediaAssets,
}: {
  packageId: string;
  mediaAssets: NormalizedPackageDetail["media_assets"];
}) {
  const { t } = useLocale();
  const [xExport, setXExport] = useState<XExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/api/v1/packages/{package_id}/x-export", { params: { path: { package_id: packageId } } })
      .then((result) => {
        if (!cancelled) setXExport(unwrap(result));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [packageId, t]);

  async function copy(text: string, index: number) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex((current) => (current === index ? null : current)), 2000);
    } catch {
      // Clipboard access can be unavailable (permissions, non-HTTPS context
      // in dev); the text is still right there on the page to select by hand.
    }
  }

  const media = xExport?.media_asset_id
    ? mediaAssets.find((asset) => asset.id === xExport.media_asset_id)
    : undefined;

  return (
    <Card>
      <h2 className="mb-1 font-medium">{t("package.x_export.title")}</h2>
      <p className="mb-2 text-xs text-muted-foreground">{t("package.x_export.hint")}</p>
      {error ? <ErrorText>{error}</ErrorText> : null}
      {xExport === null && !error ? <Spinner /> : null}
      {media?.url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={media.url} alt="" className="mb-2 max-h-48 rounded-md border border-border" />
      ) : null}
      <div className="flex flex-col gap-2">
        {xExport?.tweets.map((tweet, index) => (
          <div key={index} className="rounded-md border border-border p-2">
            <p className="whitespace-pre-wrap text-sm">{tweet}</p>
            <div className="mt-1 flex items-center justify-between">
              <span className="text-xs text-muted-foreground">{tweet.length}/280</span>
              <Button type="button" variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => copy(tweet, index)}>
                {copiedIndex === index ? t("package.x_export.copied") : t("package.x_export.copy")}
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
