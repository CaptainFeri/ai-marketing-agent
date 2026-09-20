"use client";

/**
 * Which language the panel is drawn in, and which direction that implies.
 *
 * The handoff asks for RTL/LTR support explicitly (section 4) because two of
 * the three shipped locales — Persian and Arabic — read right to left. This
 * is the one place `dir` gets decided; everything else (Tailwind's logical
 * spacing utilities, `text-start`/`text-end`) follows from the `dir`
 * attribute this sets on `<html>`, so components never branch on locale
 * themselves.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { dictionaries, type Locale, type TranslationKey } from "./translations";

const STORAGE_KEY = "ai_marketing.locale";
const RTL_LOCALES: readonly Locale[] = ["fa", "ar"];

export function directionFor(locale: Locale): "rtl" | "ltr" {
  return RTL_LOCALES.includes(locale) ? "rtl" : "ltr";
}

interface LocaleState {
  locale: Locale;
  dir: "rtl" | "ltr";
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
}

const LocaleContext = createContext<LocaleState | null>(null);

function readStoredLocale(): Locale {
  if (typeof window === "undefined") return "fa";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "fa" || stored === "en" || stored === "ar" ? stored : "fa";
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  // Lazy initializer instead of a mount effect: it runs once on the client's
  // first render, so there is no flash from "fa" to the stored locale.
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = directionFor(locale);
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private browsing or blocked storage — the choice just won't persist.
    }
  }, []);

  const t = useCallback(
    (key: TranslationKey) => dictionaries[locale][key] ?? dictionaries.en[key] ?? key,
    [locale],
  );

  const value = useMemo<LocaleState>(
    () => ({ locale, dir: directionFor(locale), setLocale, t }),
    [locale, setLocale, t],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleState {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale must be used within a LocaleProvider");
  return context;
}
