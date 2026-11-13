import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

/**
 * Fetch a GET endpoint with loading / error / reload semantics.
 * Keeps the previous page visible while reloading (no layout flash).
 */
export function useApi(path, { enabled = true, deps = [] } = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(Boolean(enabled && path));
  const [error, setError] = useState("");
  const requestId = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const result = await api.get(path);
      if (id === requestId.current) setData(result);
    } catch (err) {
      if (id === requestId.current) setError(err.message || "Unable to load data.");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path, ...deps]);

  return { data, loading, error, reload: load, setData };
}
