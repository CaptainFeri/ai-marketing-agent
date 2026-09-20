/**
 * Where the JWT pair lives in the browser.
 *
 * localStorage, not a cookie: this panel talks to the API directly from
 * client-side JavaScript on a different origin in development (handoff
 * section 4 — "the Next.js panel is served from a separate origin"), and
 * nothing here is ever read by a Next.js server component or route, so
 * there is no SSR benefit to a cookie that would outweigh the CSRF surface
 * it opens up. Guarded by `typeof window` throughout so importing this
 * module from a server component (a layout, a metadata function) never
 * throws — it simply reports "no token" there.
 */

const ACCESS_TOKEN_KEY = "ai_marketing.access_token";
const REFRESH_TOKEN_KEY = "ai_marketing.refresh_token";
const TENANT_ID_KEY = "ai_marketing.tenant_id";

export interface StoredTokens {
  accessToken: string;
  refreshToken: string;
  tenantId: string;
}

function hasWindow(): boolean {
  return typeof window !== "undefined";
}

export function setTokens(tokens: StoredTokens): void {
  if (!hasWindow()) return;
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken);
  window.localStorage.setItem(TENANT_ID_KEY, tokens.tenantId);
  window.dispatchEvent(new Event("ai_marketing:auth_changed"));
}

export function clearTokens(): void {
  if (!hasWindow()) return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  window.localStorage.removeItem(TENANT_ID_KEY);
  window.dispatchEvent(new Event("ai_marketing:auth_changed"));
}

export function getAccessToken(): string | null {
  if (!hasWindow()) return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (!hasWindow()) return null;
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function getStoredTenantId(): string | null {
  if (!hasWindow()) return null;
  return window.localStorage.getItem(TENANT_ID_KEY);
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
