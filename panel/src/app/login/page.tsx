"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useLocale } from "@/lib/locale-context";
import { ApiError } from "@/lib/api-client";
import { Button, ErrorText, Input, Spinner } from "@/components/ui";
import type { Locale } from "@/lib/translations";

export default function LoginPage() {
  const { login, status } = useAuth();
  const { t, locale, setLocale } = useLocale();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") router.replace("/workspaces");
  }, [status, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password, tenantSlug || undefined);
      router.replace("/workspaces");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.error"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">{t("login.title")}</h1>
          <select
            value={locale}
            onChange={(event) => setLocale(event.target.value as Locale)}
            className="rounded-md border border-border bg-transparent px-2 py-1 text-xs"
          >
            <option value="fa">فارسی</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </select>
        </div>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="email" className="text-sm font-medium">
              {t("login.email")}
            </label>
            <Input
              id="email"
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="password" className="text-sm font-medium">
              {t("login.password")}
            </label>
            <Input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="tenant" className="text-sm font-medium">
              {t("login.tenant")}
            </label>
            <Input
              id="tenant"
              type="text"
              value={tenantSlug}
              onChange={(event) => setTenantSlug(event.target.value)}
            />
          </div>
          {error ? <ErrorText>{error}</ErrorText> : null}
          <Button type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {t("login.submit")}
          </Button>
        </form>
      </div>
    </div>
  );
}
