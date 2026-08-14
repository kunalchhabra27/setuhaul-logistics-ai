import { useCallback, useRef, useState } from "react";

export type AsyncStatus = "idle" | "loading" | "success" | "empty" | "error";

export interface AsyncResourceState<T> {
  status: AsyncStatus;
  data: T | null;
  error: string | null;
}

interface UseAsyncResourceOptions<T> {
  /** Decide whether a successfully-loaded value counts as "empty" (renders
   * an EmptyState instead of the loaded content). Defaults to "an empty
   * array", which covers every list-shaped resource in this app; pass your
   * own for anything else (e.g. `(record) => record === null`). */
  isEmpty?: (data: T) => boolean;
}

const defaultIsEmpty = (data: unknown) => Array.isArray(data) && data.length === 0;

/**
 * Standardizes loading/success/empty/error state around an existing async
 * call -- it does not change what's fetched or how (callers still write
 * their own `listShipments()`/`fetchCheckInStatus()` etc. calls exactly as
 * before), only how the UI reacts while that call is in flight, once it
 * resolves, or if it fails.
 *
 * On error the previous `data` is kept (not cleared) so a transient refetch
 * failure -- e.g. a background poll hiccup -- doesn't blank out content the
 * user was already looking at; the caller decides whether to show the error
 * alongside stale data or instead of it.
 */
export function useAsyncResource<T>(options: UseAsyncResourceOptions<T> = {}) {
  const [state, setState] = useState<AsyncResourceState<T>>({ status: "idle", data: null, error: null });
  const isEmpty = options.isEmpty ?? defaultIsEmpty;

  // Guards against an out-of-order response from a stale run() call
  // (e.g. a fast user action fires a second run() before the first
  // resolves) overwriting the result of a newer one.
  const runIdRef = useRef(0);

  const run = useCallback(
    async (fetcher: () => Promise<T>): Promise<T | undefined> => {
      const runId = ++runIdRef.current;
      setState((prev) => ({ ...prev, status: "loading" }));
      try {
        const data = await fetcher();
        if (runId !== runIdRef.current) return undefined;
        setState({ status: isEmpty(data) ? "empty" : "success", data, error: null });
        return data;
      } catch (err) {
        if (runId !== runIdRef.current) return undefined;
        const message = err instanceof Error ? err.message : "Something went wrong. Please try again.";
        setState((prev) => ({ status: "error", data: prev.data, error: message }));
        return undefined;
      }
    },
    [isEmpty]
  );

  const reset = useCallback(() => {
    runIdRef.current += 1;
    setState({ status: "idle", data: null, error: null });
  }, []);

  return { ...state, run, reset };
}
