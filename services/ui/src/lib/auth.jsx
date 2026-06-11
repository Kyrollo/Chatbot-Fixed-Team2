/**
 * auth.jsx — Keycloak authentication context
 *
 * Wraps the app with a Keycloak provider. On mount it initializes
 * Keycloak with `check-sso` so existing sessions are restored silently.
 * Falls back to a mock auth mode when VITE_MOCK_AUTH=true (dev without Keycloak).
 */
import { createContext, useContext, useEffect, useState } from "react";

const MOCK_AUTH = import.meta.env.VITE_MOCK_AUTH === "true";

const AuthContext = createContext(null);

// ─── Mock auth (dev without Keycloak) ────────────────────────────────────────
const MOCK_USER = {
  token: "mock-dev-token",
  user_id: "dev-user-001",
  username: "dev",
  email: "dev@local",
  is_system_admin: true,
  roles: ["system_admin"],
};

function MockAuthProvider({ children }) {
  const [user] = useState(MOCK_USER);
  const logout = () => console.info("[mock auth] logout called");

  return (
    <AuthContext.Provider value={{ user, logout, loading: false }}>
      {children}
    </AuthContext.Provider>
  );
}

// ─── Real Keycloak provider ───────────────────────────────────────────────────
const KEYCLOAK_URL    = import.meta.env.VITE_KEYCLOAK_URL    || "http://localhost:8080";
const KEYCLOAK_REALM  = import.meta.env.VITE_KEYCLOAK_REALM  || "rag-system";
const KEYCLOAK_CLIENT = import.meta.env.VITE_KEYCLOAK_CLIENT || "rag-ui";

function KeycloakProvider({ children }) {
  const [state, setState] = useState({ user: null, loading: true, kc: null });

  useEffect(() => {
    let kc;
    const init = async () => {
      try {
        const Keycloak = (await import("keycloak-js")).default;
        kc = new Keycloak({
          url:      KEYCLOAK_URL,
          realm:    KEYCLOAK_REALM,
          clientId: KEYCLOAK_CLIENT,
        });

        const authenticated = await kc.init({
          onLoad:           "check-sso",
          silentCheckSsoRedirectUri: window.location.origin + "/silent-check-sso.html",
          pkceMethod:       "S256",
        });

        if (!authenticated) {
          await kc.login();
          return;
        }

        const profile = await kc.loadUserProfile();
        const roles   = kc.realmAccess?.roles ?? [];

        setState({
          loading: false,
          kc,
          user: {
            token:            kc.token,
            user_id:          kc.subject,
            username:         profile.username,
            email:            profile.email,
            is_system_admin:  roles.includes("system_admin"),
            roles,
          },
        });

        // Auto-refresh token 30 s before expiry
        setInterval(async () => {
          try {
            const refreshed = await kc.updateToken(30);
            if (refreshed) {
              setState((s) => ({
                ...s,
                user: { ...s.user, token: kc.token },
              }));
            }
          } catch {
            kc.logout();
          }
        }, 15_000);
      } catch (err) {
        console.error("Keycloak init failed:", err);
        setState({ user: null, loading: false, kc: null });
      }
    };

    init();
  }, []);

  const logout = () => state.kc?.logout({ redirectUri: window.location.origin });

  return (
    <AuthContext.Provider value={{ ...state, logout }}>
      {state.loading ? (
        <div className="flex items-center justify-center h-screen bg-surface-1">
          <div className="flex gap-2 items-center text-text-secondary text-sm">
            <span className="w-2 h-2 rounded-full bg-accent animate-pulse-dot" />
            <span className="w-2 h-2 rounded-full bg-accent animate-pulse-dot [animation-delay:0.2s]" />
            <span className="w-2 h-2 rounded-full bg-accent animate-pulse-dot [animation-delay:0.4s]" />
          </div>
        </div>
      ) : (
        children
      )}
    </AuthContext.Provider>
  );
}

// ─── Exports ─────────────────────────────────────────────────────────────────
export function AuthProvider({ children }) {
  return MOCK_AUTH ? (
    <MockAuthProvider>{children}</MockAuthProvider>
  ) : (
    <KeycloakProvider>{children}</KeycloakProvider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
