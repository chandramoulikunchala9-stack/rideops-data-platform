import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

interface Result<T> {
  key: string | null; // which request this result belongs to
  data: T | null;
  error: ApiError | null;
}

/**
 * Fetch `path` whenever it changes. Pass `null` to skip (e.g. until a selection is made).
 *
 * `loading` is derived, not stored: it is true while the stored result belongs to an older
 * request. The previous data stays visible while new filters load, and a newer request aborts
 * the older one, so a slow old response can never overwrite the latest result.
 */
export function useApi<T>(path: string | null): ApiState<T> {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T>>({ key: null, data: null, error: null });
  const key = path === null ? null : `${path}#${version}`;

  useEffect(() => {
    if (key === null || path === null) return;
    const controller = new AbortController();
    api
      .get<T>(path, controller.signal)
      .then((data) => setResult({ key, data, error: null }))
      .catch((err: unknown) => {
        if ((err as Error).name === "AbortError") return;
        const error = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
        setResult((previous) => ({ key, data: previous.data, error }));
      });
    return () => controller.abort();
  }, [key, path]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const loading = key !== null && result.key !== key;
  return { data: result.data, error: loading ? null : result.error, loading, reload };
}
