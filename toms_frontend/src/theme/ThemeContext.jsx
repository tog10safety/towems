import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";

/**
 * Appearance settings (spec §1).
 *
 * The preference is stored per account (`UserProfile.theme_preference`) so it
 * follows the user to another browser, and mirrored into localStorage so the
 * login page and the very first paint are already correct. `system` resolves
 * against the operating-system preference and keeps following it live.
 */

const STORAGE_KEY = "toms.theme";
const VALID = new Set(["system", "light", "dark"]);

const ThemeContext = createContext(null);

export function readStoredTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return VALID.has(value) ? value : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference) {
  if (preference === "light" || preference === "dark") return preference;
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(preference) {
  const resolved = resolveTheme(preference);
  document.documentElement.dataset.theme = resolved;
  return resolved;
}

export function ThemeProvider({ children }) {
  const { user } = useAuth();
  const [preference, setPreference] = useState(readStoredTheme);
  const [resolved, setResolved] = useState(() => resolveTheme(readStoredTheme()));
  const [error, setError] = useState("");
  // The server value for the signed-in account is applied once per account so
  // an unsaved local choice is not overwritten on every render.
  const syncedUser = useRef(null);

  useEffect(() => {
    const resolvedTheme = applyTheme(preference);
    setResolved(resolvedTheme);
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      /* private mode: the in-memory preference still works */
    }

    if (preference !== "system" || !window.matchMedia) return undefined;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setResolved(applyTheme("system"));
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, [preference]);

  useEffect(() => {
    const saved = user?.preferences?.theme;
    if (!user) {
      syncedUser.current = null;
      return;
    }
    if (!VALID.has(saved)) return;
    if (syncedUser.current === user.id) return;
    syncedUser.current = user.id;
    if (saved !== preference) setPreference(saved);
    // Only re-run when the account or its saved preference changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, user?.preferences?.theme]);

  const setTheme = useCallback(
    async (next) => {
      if (!VALID.has(next)) return;
      setPreference(next);
      setError("");
      if (!user) return;
      try {
        await api.patch("/auth/preferences/", { theme: next });
      } catch (err) {
        setError(err.message || "The appearance preference could not be saved.");
      }
    },
    [user]
  );

  const toggleTheme = useCallback(() => {
    const next = resolveTheme(preference) === "dark" ? "light" : "dark";
    return setTheme(next);
  }, [preference, setTheme]);

  const value = useMemo(
    () => ({ theme: preference, resolvedTheme: resolved, setTheme, toggleTheme, error }),
    [preference, resolved, setTheme, toggleTheme, error]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside ThemeProvider");
  return context;
}
