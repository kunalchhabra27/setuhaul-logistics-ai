import { getSession } from "../auth/authService";

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
  async function performRequest(path: string, init?: RequestInit, forceRefresh = false) {
    const session = await getSession(serviceId, forceRefresh ? { forceRefresh: true } : undefined);
    const token = session?.access_token ?? null;
    return fetch(`${baseUrl}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      ...init,
    });
  }

  async function parsePayload(response: Response) {
    const contentType = response.headers.get("content-type") ?? "";
    return contentType.includes("application/json")
      ? await response.json()
      : await response.text();
  }

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response = await performRequest(path, init);
    if (response.status === 401) {
      response = await performRequest(path, init, true);
    }

    const payload = await parsePayload(response);

    if (!response.ok) {
      const message = (() => {
        if (typeof payload === "object" && payload && "detail" in payload) {
          const detail = (payload as { detail?: unknown }).detail;
          if (typeof detail === "string") return detail;
          if (detail && typeof detail === "object" && "message" in detail) {
            return String((detail as { message?: unknown }).message ?? "Request failed");
          }
          return "Request failed";
        }
        if (typeof payload === "object" && payload && "error" in payload) {
          return String((payload as { error?: { message?: unknown } }).error?.message ?? "Request failed");
        }
        return "Request failed";
      })();
      throw new ApiClientError(message, response.status, payload);
    }

    return payload as T;
  }

  return { request };
}

export function apiUrl(path: string) {
  return `${baseUrl}${path}`;
}
