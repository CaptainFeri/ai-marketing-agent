"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Badge, Button, Card, ErrorText, Input, Spinner, Textarea } from "@/components/ui";

type AnalyticsCredentialOut = components["schemas"]["AnalyticsCredentialOut"];
type AnalyticsProvider = components["schemas"]["AnalyticsProvider"];

const PROVIDERS: AnalyticsProvider[] = ["search_console", "ga4"];

export default function AnalyticsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { t } = useLocale();
  const [credentials, setCredentials] = useState<AnalyticsCredentialOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pullResult, setPullResult] = useState<number | null>(null);

  const [provider, setProvider] = useState<AnalyticsProvider>("search_console");
  const [label, setLabel] = useState("default");
  const [siteUrl, setSiteUrl] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [serviceAccountJson, setServiceAccountJson] = useState("");

  async function load() {
    try {
      const result = await apiClient.GET("/api/v1/workspaces/{workspace_id}/analytics-credentials", {
        params: { path: { workspace_id: workspaceId } },
      });
      setCredentials(unwrap(result));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  useEffect(() => {
    // Fetch-on-mount: `load` is also called after each mutation below, so it
    // stays a named function rather than being inlined into the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let serviceAccount: unknown;
      try {
        serviceAccount = JSON.parse(serviceAccountJson);
      } catch {
        throw new Error("Service account key is not valid JSON");
      }
      const payload =
        provider === "search_console"
          ? { site_url: siteUrl, service_account: serviceAccount }
          : { property_id: propertyId, service_account: serviceAccount };
      const publicMetadata =
        provider === "search_console" ? { site_url: siteUrl } : { property_id: propertyId };

      await apiClient.POST("/api/v1/workspaces/{workspace_id}/analytics-credentials", {
        params: { path: { workspace_id: workspaceId } },
        body: { provider, label, payload, public_metadata: publicMetadata },
      });
      setSiteUrl("");
      setPropertyId("");
      setServiceAccountJson("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(credentialId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiClient.DELETE(
        "/api/v1/workspaces/{workspace_id}/analytics-credentials/{credential_id}",
        { params: { path: { workspace_id: workspaceId, credential_id: credentialId } } },
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function pullNow() {
    setBusy(true);
    setError(null);
    setPullResult(null);
    try {
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/analytics-credentials/pull", {
        params: { path: { workspace_id: workspaceId } },
        body: {},
      });
      const body = unwrap(result) as { snapshots_written: number };
      setPullResult(body.snapshots_written);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">{t("analytics.title")}</h1>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={pullNow}>
            {busy ? <Spinner /> : null}
            {t("analytics.pull_now")}
          </Button>
          {pullResult !== null ? (
            <span className="text-sm text-zinc-500">
              {t("analytics.pulled")}: {pullResult}
            </span>
          ) : null}
        </div>
      </div>

      <Card className="mb-4">
        <form onSubmit={onCreate} className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <select
              value={provider}
              onChange={(event) => setProvider(event.target.value as AnalyticsProvider)}
              className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <Input
              placeholder={t("channels.label")}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              className="max-w-40"
            />
          </div>

          {provider === "search_console" ? (
            <Input
              placeholder="site_url (https://example.com/)"
              value={siteUrl}
              onChange={(event) => setSiteUrl(event.target.value)}
              required
            />
          ) : (
            <Input
              placeholder="property_id"
              value={propertyId}
              onChange={(event) => setPropertyId(event.target.value)}
              required
            />
          )}

          <Textarea
            placeholder='{"client_email": "...", "private_key": "..."} — the service account key file from Google Cloud Console'
            rows={5}
            value={serviceAccountJson}
            onChange={(event) => setServiceAccountJson(event.target.value)}
            required
            className="font-mono text-xs"
          />

          <div>
            <Button type="submit" disabled={busy}>
              {busy ? <Spinner /> : null}
              {t("analytics.new")}
            </Button>
          </div>
        </form>
      </Card>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {credentials === null && !error ? <Spinner /> : null}
      {credentials?.length === 0 ? <p className="text-sm text-zinc-500">{t("analytics.empty")}</p> : null}

      <div className="flex flex-col gap-2">
        {credentials?.map((credential) => (
          <Card key={credential.id} className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">
                {credential.provider} · {credential.label}
              </p>
              <p className="text-xs text-zinc-500">{JSON.stringify(credential.public_metadata)}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={credential.is_active ? "good" : "neutral"}>
                {credential.is_active ? t("analytics.active") : t("analytics.inactive")}
              </Badge>
              {credential.is_active ? (
                <Button
                  type="button"
                  variant="danger"
                  disabled={busy}
                  onClick={() => deactivate(credential.id)}
                >
                  {t("analytics.deactivate")}
                </Button>
              ) : null}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
