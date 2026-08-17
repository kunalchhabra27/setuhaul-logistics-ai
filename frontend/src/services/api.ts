import { getAccessToken, refreshAccessToken } from "../auth/authService";

const baseUrl = (import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "") + "/api/v1";

// FastAPI's own validation errors (422) put `detail` as an ARRAY of
// {msg, loc, type} objects, not a string -- the old code did
// `String(payload.detail)` unconditionally, which for an array of objects
// produces the literal text "[object Object]" (Array.prototype.toString
// stringifies each element, and a plain object's default toString is
// "[object Object]"). That's the "[object Object]" toast drivers could see
// on a malformed/failed request. This handles every shape our backend (or
// FastAPI itself) can send: a plain string detail, a FastAPI validation
// array, our own {error:{message}} shape, or anything else.
function extractErrorMessage(payload: unknown): string {
  if (typeof payload !== "object" || payload === null) return "Request failed";
  if ("detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) => (typeof item === "object" && item && "msg" in item ? String((item as { msg?: unknown }).msg) : null))
        .filter((m): m is string => !!m);
      if (msgs.length) return msgs.join("; ");
    }
  }
  if ("error" in payload) {
    const error = (payload as { error?: { message?: unknown } }).error;
    if (error && typeof error === "object" && "message" in error) return String(error.message ?? "Request failed");
  }
  return "Request failed";
}

export class ApiClientError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload?: unknown) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.payload = payload;
  }
}

// Distinguishes "the server never got a chance to respond" (timeout, dropped
// connection, CORS failure, etc.) from an HTTP-level error, so callers can
// show a message like "try again" instead of one implying something the
// backend actually rejected.
export class ApiNetworkError extends Error {
  cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "ApiNetworkError";
    this.cause = cause;
  }
}

type RequestOptions = RequestInit & {
  // Bounds how long this specific call waits for a response before the
  // request is aborted client-side. Left unset for short-lived endpoints
  // (profile fetch, snapshot poll, dock actions) so they keep the browser's
  // own default behavior; callers with a genuinely slow backend path (the
  // chat send, which can involve an LLM/tool-calling round trip) pass an
  // explicit budget instead of every endpoint being forced onto one global
  // timeout that's either too short for chat or needlessly long for
  // everything else.
  timeoutMs?: number;
};

// Each portal (drivers/tms/wms/checkin) gets its own API client bound to that
// portal's own stored access token -- so a TMS request always carries the TMS
// session's token, never a token borrowed from whichever portal logged in most
// recently. This is what lets the four portals be used in parallel in one
// browser without one login clobbering another's outgoing requests.
export function createApiClient(serviceId: string) {
  async function request<T>(path: string, init?: RequestOptions, isRetry = false): Promise<T> {
    const token = getAccessToken(serviceId);
    const { timeoutMs, ...fetchInit } = init ?? {};

    const timeoutController = timeoutMs !== undefined ? new AbortController() : null;
    const timeoutId =
      timeoutController !== null ? window.setTimeout(() => timeoutController.abort(), timeoutMs) : null;

    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(fetchInit.headers ?? {}),
        },
        ...fetchInit,
        signal: timeoutController?.signal ?? fetchInit.signal,
      });
    } catch (err) {
      // A raw fetch() rejection means the request never got a response at
      // all -- a dropped connection, CORS failure, DNS error, or (if we
      // aborted it above) a client-side timeout. This is exactly the case
      // that previously surfaced the browser's own "TypeError: Failed to
      // fetch" string verbatim in a toast; log the real error for
      // diagnostics and hand callers a message that's actually actionable
      // for a driver.
      const isAbort = err instanceof DOMException && err.name === "AbortError";
      console.error(`API request failed: ${path}`, err);
      throw new ApiNetworkError(
        isAbort
          ? "The dispatch assistant is taking longer than expected. Please try again."
          : "Lost connection to dispatch. Check your network and try again.",
        err
      );
    } finally {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
    }

    const contentType = response.headers.get("content-type") ?? "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      // A 401 usually means the access token expired between page load and
      // this request (the proactive refresh timer in supabaseClient.ts can
      // miss its window on a throttled/backgrounded tab). Try refreshing
      // the session once and replaying the request before surfacing an
      // error -- this is what previously made the chatbot look "not
      // responding": the request silently failed with an expired token and
      // only a toast (easy to miss) told the driver anything went wrong.
      if (response.status === 401 && !isRetry && token) {
        const refreshed = await refreshAccessToken(serviceId);
        if (refreshed) return request<T>(path, init, true);
      }

      const message = extractErrorMessage(payload);
      throw new ApiClientError(message, response.status, payload);
    }

    return payload as T;
  }

  // For binary responses (e.g. an .xlsx export) that api.request's
  // JSON/text parsing can't handle. Shares the same auth-header and
  // refresh-on-401 behavior as request() above so a downloaded report
  // doesn't silently fail just because the token happened to expire.
  async function requestBlob(
    path: string,
    init?: RequestInit,
    isRetry = false
  ): Promise<{ blob: Blob; filename: string | null }> {
    const token = getAccessToken(serviceId);
    const response = await fetch(`${baseUrl}${path}`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      ...init,
    });

    if (!response.ok) {
      if (response.status === 401 && !isRetry && token) {
        const refreshed = await refreshAccessToken(serviceId);
        if (refreshed) return requestBlob(path, init, true);
      }
      throw new ApiClientError("Request failed", response.status);
    }

    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename="?([^";]+)"?/.exec(disposition);
    return { blob: await response.blob(), filename: match?.[1] ?? null };
  }

  return { request, requestBlob };
}

export function apiUrl(path: string) {
  return `${baseUrl}${path}`;
}
