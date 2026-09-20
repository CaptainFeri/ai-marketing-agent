"use client";

import { useEffect, useState } from "react";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Card, ErrorText, Spinner } from "@/components/ui";

type QuotaStatus = components["schemas"]["QuotaStatus"];

export default function QuotaPage() {
  const { t } = useLocale();
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/api/v1/gpu/quota")
      .then((result) => {
        if (!cancelled) setQuota(unwrap(result));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  if (error) return <ErrorText>{error}</ErrorText>;
  if (!quota) return <Spinner />;

  const consumedPct = quota.allocated_seconds > 0 ? Math.min(100, (quota.consumed_seconds / quota.allocated_seconds) * 100) : 0;

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-4 text-lg font-semibold">{t("quota.title")}</h1>
      <Card className="mb-4">
        <div className="mb-2 flex justify-between text-sm">
          <span>{t("quota.consumed")}</span>
          <span>
            {quota.consumed_seconds.toFixed(0)}s / {quota.allocated_seconds.toFixed(0)}s
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-surface">
          <div className="h-full bg-accent" style={{ width: `${consumedPct}%` }} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm text-zinc-500 sm:grid-cols-4">
          <div>
            <p className="text-xs">{t("quota.capacity")}</p>
            <p>{quota.capacity_seconds.toFixed(0)}s</p>
          </div>
          <div>
            <p className="text-xs">{t("quota.remaining")}</p>
            <p>{quota.remaining_seconds.toFixed(0)}s</p>
          </div>
          <div>
            <p className="text-xs">weight</p>
            <p>{quota.weight}</p>
          </div>
          <div>
            <p className="text-xs">reserved</p>
            <p>{quota.reserved_seconds.toFixed(0)}s</p>
          </div>
        </div>
      </Card>

      <Card>
        <h2 className="mb-2 font-medium">{t("quota.affordances")}</h2>
        <div className="flex flex-col gap-2">
          {quota.affordances.map((affordance) => (
            <div key={affordance.label} className="flex items-center justify-between text-sm">
              <span>{affordance.label}</span>
              <span className="text-zinc-500">
                {affordance.affordable_today} × ({affordance.estimated_seconds_each.toFixed(0)}s)
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
