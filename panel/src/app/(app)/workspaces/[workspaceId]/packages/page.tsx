"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Badge, Button, Card, ErrorText, Input, Spinner } from "@/components/ui";

type PackageOut = components["schemas"]["PackageOut"];
type PackageStatus = components["schemas"]["PackageStatus"];

const STATUS_TONE: Record<PackageStatus, "neutral" | "good" | "bad" | "warn"> = {
  planned: "neutral",
  drafting: "neutral",
  text_review: "warn",
  rejected: "bad",
  media_generating: "neutral",
  selection: "warn",
  scheduled: "good",
  published: "good",
  measuring: "good",
  refresh: "warn",
  failed: "bad",
};

export default function PackagesPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { t } = useLocale();
  const [packages, setPackages] = useState<PackageOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [pkgLocale, setPkgLocale] = useState("fa");

  async function load() {
    try {
      const result = await apiClient.GET("/api/v1/packages", {
        params: { query: { workspace_id: workspaceId } },
      });
      setPackages(unwrap(result).items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  useEffect(() => {
    // Fetch-on-mount: `load` is also called after creating a package, so it
    // stays a named function rather than being inlined into the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      await apiClient.POST("/api/v1/packages", {
        body: { workspace_id: workspaceId, title, locale: pkgLocale },
      });
      setTitle("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-4 text-lg font-semibold">{t("packages.title")}</h1>

      <Card className="mb-4">
        <form onSubmit={onCreate} className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-zinc-500">{t("packages.title")}</label>
            <Input value={title} onChange={(event) => setTitle(event.target.value)} required className="min-w-64" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-zinc-500">{t("packages.locale")}</label>
            <select
              value={pkgLocale}
              onChange={(event) => setPkgLocale(event.target.value)}
              className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
            >
              <option value="fa">fa</option>
              <option value="en">en</option>
              <option value="ar">ar</option>
            </select>
          </div>
          <Button type="submit" disabled={creating}>
            {creating ? <Spinner /> : null}
            {t("packages.new")}
          </Button>
        </form>
      </Card>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {packages === null && !error ? <Spinner /> : null}
      {packages?.length === 0 ? <p className="text-sm text-zinc-500">{t("packages.empty")}</p> : null}

      <div className="flex flex-col gap-2">
        {packages?.map((pkg) => (
          <Link key={pkg.id} href={`/workspaces/${workspaceId}/packages/${pkg.id}`}>
            <Card className="flex items-center justify-between transition hover:border-accent">
              <div>
                <p className="font-medium">{pkg.title}</p>
                <p className="text-xs text-zinc-500">
                  {pkg.locale} · {new Date(pkg.created_at).toLocaleString()}
                </p>
              </div>
              <Badge tone={STATUS_TONE[pkg.status]}>{pkg.status}</Badge>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
