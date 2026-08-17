import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientError, ApiNetworkError, createApiClient } from "./api";

// Regression coverage for the "raw browser error string surfaced verbatim"
// bug: a dropped connection previously threw the literal
// `TypeError: Failed to fetch` up to the UI, which is what QA saw as the red
// toast. These tests pin down that api.ts now classifies that case (and a
// client-side timeout) into an ApiNetworkError with a safe, actionable
// message instead.
describe("createApiClient error normalization", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("normalizes a raw fetch() network failure instead of leaking the browser's error text", async () => {
    const rawError = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(rawError));

    const client = createApiClient("test-drivers");

    await expect(client.request("/driver-chat-eta/chat", { method: "POST" })).rejects.toMatchObject({
      name: "ApiNetworkError",
    });

    try {
      await client.request("/driver-chat-eta/chat", { method: "POST" });
      throw new Error("expected request() to reject");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiNetworkError);
      expect((err as ApiNetworkError).message).not.toContain("TypeError");
      expect((err as ApiNetworkError).message).not.toContain("Failed to fetch");
      expect((err as ApiNetworkError).cause).toBe(rawError);
    }
  });

  it("aborts a request past its timeoutMs and reports a timeout-specific message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              reject(new DOMException("The operation was aborted.", "AbortError"));
            });
          })
      )
    );

    const client = createApiClient("test-drivers");

    try {
      await client.request("/driver-chat-eta/chat", { method: "POST", timeoutMs: 5 });
      throw new Error("expected request() to reject");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiNetworkError);
      expect((err as ApiNetworkError).message).toMatch(/taking longer than expected/i);
    }
  });

  it("still surfaces structured HTTP error responses as ApiClientError (unchanged behavior)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Shipment not found" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        })
      )
    );

    const client = createApiClient("test-drivers");
    await expect(client.request("/driver-chat-eta/snapshot")).rejects.toBeInstanceOf(ApiClientError);
  });
});
