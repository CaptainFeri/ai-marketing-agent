/**
 * The one place that talks to the backend.
 *
 * Types come from `api-schema.ts`, generated from the FastAPI app's own
 * OpenAPI document (`npm run gen:api`) — never hand-edited, so a route or a
 * field the backend renamed shows up here as a type error instead of a
 * runtime one. See `docs/api-client.md` for how to regenerate it.
 *
 * Every request carries the access token when one is held, and a single
 * 401 anywhere triggers one refresh attempt before the caller sees it — the
 * same "app.tenant_id must be bound or the request fails closed" posture
 * the backend takes, mirrored here as "a request never silently runs
 * unauthenticated after the token has expired".
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./api-schema";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./token-storage";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/** The shape every FastAPI error response carries (see app/main.py). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  request_id: string | null;
}

/** Thrown for any non-2xx response, with the backend's own message and code
 * carried through rather than flattened into a generic "request failed". */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(status: number, body: Partial<ApiErrorBody> | undefined) {
    const message = body?.error?.message ?? `request failed with status ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown_error";
    this.details = body?.error?.details ?? {};
    this.requestId = body?.request_id ?? null;
  }
}

/** True once a refresh attempt has already failed this session, so a
 * cascade of 401s from several in-flight requests does not each try to
 * refresh (and each redirect to /login) independently. */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async (response) => {
        if (!response.ok) return false;
        const body = await response.json();
        setTokens({
          accessToken: body.access_token,
          refreshToken: body.refresh_token,
          tenantId: body.tenant_id,
        });
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getAccessToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ request, response }) {
    // /auth/login and /auth/refresh themselves must not trigger a refresh
    // loop on their own 401.
    const isAuthRoute = request.url.includes("/auth/login") || request.url.includes("/auth/refresh");
    if (response.status !== 401 || isAuthRoute) return response;

    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      clearTokens();
      return response;
    }

    const retried = request.clone();
    retried.headers.set("Authorization", `Bearer ${getAccessToken()}`);
    return fetch(retried);
  },
};

export const apiClient = createClient<paths>({ baseUrl: API_BASE_URL });
apiClient.use(authMiddleware);

/** Unwraps an openapi-fetch result into either the data or a thrown
 * {@link ApiError} — every call site awaits one thing instead of checking
 * `error`/`data` by hand every time. */
export function unwrap<T>(result: { data?: T; response: Response; error?: unknown }): T {
  if (result.data !== undefined) return result.data;
  throw new ApiError(result.response.status, result.error as Partial<ApiErrorBody>);
}
