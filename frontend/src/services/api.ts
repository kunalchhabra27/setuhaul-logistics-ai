import { getAccessToken, refreshAccessToken } from "../auth/authService";

const baseUrl = (import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "") + "/api/v1";

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

// Each portal (drivers/tms/wms/checkin) gets its own API client bound to that
// portal's own stored access token -- so a TMS request always carries the TMS
// session's token, never a token borrowed from whichever portal logged in most
// recently. This is what lets the four portals be used in parallel in one
// browser without one login clobbering another's outgoing requests.
export function createApiClient(serviceId: string) {
  async function request<T>(path: string, init?: RequestInit, isRetry = false): Promise<T> {
    const token = getAccessToken(serviceId);
    const response = await fetch(`${baseUrl}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      ...init,
    });

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

      const message =
        typeof payload === "object" && payload && "detail" in payload
          ? String((payload as { detail?: unknown }).detail)
          : typeof payload === "object" && payload && "error" in payload
            ? String((payload as { error?: { message?: unknown } }).error?.message ?? "Request failed")
            : "Request failed";
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
