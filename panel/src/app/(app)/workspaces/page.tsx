"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient, ApiError, unwrap } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useLocale } from "@/lib/locale-context";
import { Card, ErrorText, Spinner } from "@/components/ui";

type WorkspaceOut = components["schemas"]["WorkspaceOut"];

export default function WorkspacesPage() {
  const { t } = useLocale();
  const [workspaces, setWorkspaces] = useState<WorkspaceOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .GET("/api/v1/workspaces")
      .then((result) => {
        if (!cancelled) setWorkspaces(unwrap(result));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-lg font-semibold">{t("workspaces.title")}</h1>
      {error ? <ErrorText>{error}</ErrorText> : null}
      {workspaces === null && !error ? <Spinner /> : null}
      {workspaces?.length === 0 ? <p className="text-sm text-zinc-500">{t("workspaces.empty")}</p> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {workspaces?.map((workspace) => (
          <Link key={workspace.id} href={`/workspaces/${workspace.id}/questionnaire`}>
            <Card className="transition hover:border-accent">
              <p className="font-medium">{workspace.name}</p>
              <p className="text-sm text-zinc-500">{workspace.slug}</p>
              <p className="mt-1 text-xs text-zinc-500">
                {workspace.locales.join(", ")} · {workspace.calendar}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
