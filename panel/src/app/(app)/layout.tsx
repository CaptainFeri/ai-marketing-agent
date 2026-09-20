"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useLocale } from "@/lib/locale-context";
import type { Locale } from "@/lib/translations";
import { Spinner } from "@/components/ui";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status, session, logout } = useAuth();
  const { t, locale, setLocale } = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ workspaceId?: string }>();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const workspaceId = params?.workspaceId;
  const navLink = (href: string, label: string) => (
    <Link
      href={href}
      className={`rounded-md px-3 py-1.5 text-sm ${
        pathname === href ? "bg-accent text-accent-foreground" : "hover:bg-surface"
      }`}
    >
      {label}
    </Link>
  );

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/workspaces" className="me-2 text-sm font-semibold">
            {t("app.title")}
          </Link>
          {navLink("/workspaces", t("nav.workspaces"))}
          {workspaceId ? (
            <>
              {navLink(`/workspaces/${workspaceId}/questionnaire`, t("nav.questionnaire"))}
              {navLink(`/workspaces/${workspaceId}/packages`, t("nav.packages"))}
              {navLink(`/workspaces/${workspaceId}/channels`, t("nav.channels"))}
              {navLink(`/workspaces/${workspaceId}/analytics`, t("nav.analytics"))}
              {navLink(`/workspaces/${workspaceId}/quota`, t("nav.quota"))}
            </>
          ) : null}
        </div>
        <div className="flex items-center gap-3 text-sm">
          <select
            value={locale}
            onChange={(event) => setLocale(event.target.value as Locale)}
            className="rounded-md border border-border bg-transparent px-2 py-1 text-xs"
          >
            <option value="fa">فارسی</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </select>
          <span className="text-zinc-500">{session?.user.email}</span>
          <button
            type="button"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="rounded-md px-3 py-1.5 hover:bg-surface"
          >
            {t("nav.logout")}
          </button>
        </div>
      </header>
      <main className="flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
