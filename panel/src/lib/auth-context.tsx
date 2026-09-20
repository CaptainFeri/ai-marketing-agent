"use client";

/**
 * Who is signed in, and what role they hold — read fresh on every reload
 * from `GET /auth/me` rather than trusted from the JWT's own claims, the
 * same reasoning `app.api.deps.get_principal` applies server-side: a role
 * change or a revoked membership must take effect on the next request, not
 * whenever the token happens to expire.
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
import { apiClient, unwrap } from "./api-client";
import type { components } from "./api-schema";
import { clearTokens, getAccessToken, isAuthenticated, setTokens } from "./token-storage";

type SessionInfo = components["schemas"]["SessionInfo"];
type Role = components["schemas"]["Role"];

/** Owner > admin > editor > viewer — mirrors `app.db.enums.Role.rank`. */
const ROLE_RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };

interface AuthState {
  status: "loading" | "authenticated" | "unauthenticated";
  session: SessionInfo | null;
  login: (email: string, password: string, tenantSlug?: string) => Promise<void>;
  logout: () => void;
  /** The caller's role for one workspace (falling back to a tenant-wide
   * membership), or null with no access at all — mirrors
   * `app.services.auth.effective_role`. */
  roleFor: (workspaceId: string | null) => Role | null;
  /** True when `roleFor(workspaceId)` is at least `minimum`. */
  hasRole: (workspaceId: string | null, minimum: Role) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [session, setSession] = useState<SessionInfo | null>(null);

  const loadSession = useCallback(async () => {
    if (!isAuthenticated()) {
      setStatus("unauthenticated");
      setSession(null);
      return;
    }
    try {
      const result = await apiClient.GET("/api/v1/auth/me");
      setSession(unwrap(result));
      setStatus("authenticated");
    } catch {
      // The token was rejected and the refresh middleware could not save
      // it — getAccessToken() below will now be null.
      clearTokens();
      setSession(null);
      setStatus(getAccessToken() ? "loading" : "unauthenticated");
    }
  }, []);

  useEffect(() => {
    // Load once on mount; `loadSession` is reused below for cross-tab sync.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
    // Another tab logging out (or in) updates this one too.
    const onChange = () => void loadSession();
    window.addEventListener("ai_marketing:auth_changed", onChange);
    return () => window.removeEventListener("ai_marketing:auth_changed", onChange);
  }, [loadSession]);

  const login = useCallback(
    async (email: string, password: string, tenantSlug?: string) => {
      const result = await apiClient.POST("/api/v1/auth/login", {
        body: { email, password, tenant_slug: tenantSlug ?? null },
      });
      const tokens = unwrap(result);
      setTokens({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        tenantId: tokens.tenant_id,
      });
      await loadSession();
    },
    [loadSession],
  );

  const logout = useCallback(() => {
    clearTokens();
    setSession(null);
    setStatus("unauthenticated");
  }, []);

  const roleFor = useCallback(
    (workspaceId: string | null): Role | null => {
      if (!session) return null;
      let best: Role | null = null;
      for (const membership of session.memberships) {
        const applies = membership.workspace_id === null || membership.workspace_id === workspaceId;
        if (!applies) continue;
        if (best === null || ROLE_RANK[membership.role] > ROLE_RANK[best]) {
          best = membership.role;
        }
      }
      return best;
    },
    [session],
  );

  const hasRole = useCallback(
    (workspaceId: string | null, minimum: Role): boolean => {
      const role = roleFor(workspaceId);
      return role !== null && ROLE_RANK[role] >= ROLE_RANK[minimum];
    },
    [roleFor],
  );

  const value = useMemo<AuthState>(
    () => ({ status, session, login, logout, roleFor, hasRole }),
    [status, session, login, logout, roleFor, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
