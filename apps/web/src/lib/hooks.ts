import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";
import type { TeamLive } from "./types";

/** Load data; reload when deps change, and optionally every `everyMs`. */
export function useData<T>(load: () => Promise<T>, deps: unknown[], everyMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const fn = useRef(load);
  fn.current = load;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fn.current());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    reload();
    if (!everyMs) return;
    const t = setInterval(reload, everyMs);
    return () => clearInterval(t);
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, loading, reload, setData };
}

/** The teams and collectors the user may see (from the live view; cheap). */
export function useRoster() {
  return useData(() => get<TeamLive>("/team/live"), []);
}
