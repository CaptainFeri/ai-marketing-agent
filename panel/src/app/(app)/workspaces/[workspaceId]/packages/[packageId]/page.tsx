"use client";

import { useEffect, useState } from "react";
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
};
type Publication = components["schemas"]["PublicationOut"];
type ApprovalGate = components["schemas"]["ApprovalGate"];
type ApprovalDecision = components["schemas"]["ApprovalDecision"];
type PipelineStep = components["schemas"]["PipelineStep"];

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
  const { packageId } = useParams<{ workspaceId: string; packageId: string }>();
  const { t } = useLocale();
  const [pkg, setPkg] = useState<NormalizedPackageDetail | null>(null);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const [detail, pubs] = await Promise.all([
        apiClient.GET("/api/v1/packages/{package_id}", { params: { path: { package_id: packageId } } }),
        apiClient.GET("/api/v1/packages/{package_id}/publications", {
          params: { path: { package_id: packageId } },
        }),
      ]);
      const data = unwrap(detail);
      setPkg({
        ...data,
        step_runs: data.step_runs ?? [],
        variants: data.variants ?? [],
        media_assets: data.media_assets ?? [],
      });
      setPublications(unwrap(pubs));
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
  }, [packageId]);

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

      <PublicationsPanel
        packageId={packageId}
        publications={publications}
        variants={pkg.variants}
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
  busy,
  onScheduled,
  onCancel,
}: {
  packageId: string;
  publications: Publication[];
  variants: NormalizedPackageDetail["variants"];
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
          <Input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} />
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
              {pub.channel} · {new Date(pub.scheduled_at).toLocaleString()}
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
