import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, ensureCsrf } from "../api/client.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const me = await api.get("/auth/me/");
      setUser(me);
      setError("");
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) {
        // 403 for anonymous is also expected on some deployments.
        if (!(err instanceof ApiError) || err.status !== 403) {
          setError(err.message || "Unable to reach the server.");
        }
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (username, password) => {
    await ensureCsrf();
    const me = await api.post("/auth/login/", { username, password });
    // A password for an enrolled account returns a challenge, not a session.
    // Only a real challenge leaves ``user`` unset; an unenrolled account gets
    // a session plus the setup flags the shell redirects on.
    if (me?.challenge) return me;
    setUser(me);
    setError("");
    return me;
  }, []);

  const verifyMfa = useCallback(async (challenge, code, recovery = false) => {
    const me = await api.post("/auth/mfa/challenge/", { challenge, code, recovery });
    setUser(me);
    setError("");
    return me;
  }, []);

  const verifyMfaSetup = useCallback(async (code) => {
    const me = await api.post("/auth/mfa/setup/verify/", { code });
    setUser(me);
    setError("");
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout/", {});
    } catch {
      /* logging out locally is still correct if the session already expired */
    }
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      login,
      verifyMfa,
      verifyMfaSetup,
      logout,
      refresh,
      permissions: user?.permissions ?? {},
    }),
    [user, loading, error, login, verifyMfa, verifyMfaSetup, logout, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
