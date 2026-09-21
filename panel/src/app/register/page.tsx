"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useLocale } from "@/lib/locale-context";
import { ApiError } from "@/lib/api-client";
import { Button, ErrorText, Input, Spinner } from "@/components/ui";

const SLUG_PATTERN = /^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$/;

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

export default function RegisterPage() {
  const { register, status } = useAuth();
  const { t } = useLocale();
  const router = useRouter();

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") router.replace("/workspaces");
  }, [status, router]);

  function onNameChange(value: string) {
    setName(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await register({ slug, name, ownerEmail: email, ownerPassword: password, ownerFullName: fullName || undefined });
      router.replace("/workspaces");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("register.error"));
    } finally {
      setSubmitting(false);
    }
  }

  const slugValid = SLUG_PATTERN.test(slug);

  return (
    <div className="flex flex-1 items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-xl font-semibold">{t("register.title")}</h1>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="name" className="text-sm font-medium">
              {t("register.company_name")}
            </label>
            <Input id="name" required value={name} onChange={(event) => onNameChange(event.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="slug" className="text-sm font-medium">
              {t("register.slug")}
            </label>
            <Input
              id="slug"
              required
              value={slug}
              onChange={(event) => {
                setSlugTouched(true);
                setSlug(event.target.value.toLowerCase());
              }}
            />
            <p className="text-xs text-muted-foreground">{t("register.slug_hint")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="fullName" className="text-sm font-medium">
              {t("register.full_name")}
            </label>
            <Input id="fullName" value={fullName} onChange={(event) => setFullName(event.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="email" className="text-sm font-medium">
              {t("register.email")}
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
              {t("register.password")}
            </label>
            <Input
              id="password"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error ? <ErrorText>{error}</ErrorText> : null}
          <Button type="submit" disabled={submitting || !slugValid}>
            {submitting ? <Spinner /> : null}
            {t("register.submit")}
          </Button>
        </form>
        <p className="mt-4 text-sm text-muted-foreground">
          {t("register.login_prompt")}{" "}
          <Link href="/login" className="font-medium text-foreground underline">
            {t("register.login_link")}
          </Link>
        </p>
      </div>
    </div>
  );
}
