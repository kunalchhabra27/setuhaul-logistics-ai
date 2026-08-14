import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useAsyncResource } from "./useAsyncResource";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsyncResource", () => {
  it("starts idle, then loading, then success with data", async () => {
    const { result } = renderHook(() => useAsyncResource<{ value: string }>());
    expect(result.current.status).toBe("idle");

    const d = deferred<{ value: string }>();
    let runPromise!: Promise<unknown>;
    act(() => {
      runPromise = result.current.run(() => d.promise);
    });
    expect(result.current.status).toBe("loading");

    await act(async () => {
      d.resolve({ value: "hello" });
      await runPromise;
    });

    expect(result.current.status).toBe("success");
    expect(result.current.data).toEqual({ value: "hello" });
    expect(result.current.error).toBeNull();
  });

  it("reports 'empty' when isEmpty says so, not 'success'", async () => {
    const { result } = renderHook(() =>
      useAsyncResource<{ items: string[] }>({ isEmpty: (data) => data.items.length === 0 })
    );

    await act(async () => {
      await result.current.run(async () => ({ items: [] }));
    });

    expect(result.current.status).toBe("empty");
  });

  it("moves to 'error' and keeps the message, but does not fabricate data", async () => {
    const { result } = renderHook(() => useAsyncResource<string>());

    await act(async () => {
      await result.current.run(async () => {
        throw new Error("boom");
      });
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("boom");
    expect(result.current.data).toBeNull();
  });

  it("keeps stale data visible after a later failed run, instead of clearing it", async () => {
    const { result } = renderHook(() => useAsyncResource<string>());

    await act(async () => {
      await result.current.run(async () => "first-good-value");
    });
    expect(result.current.data).toBe("first-good-value");

    await act(async () => {
      await result.current.run(async () => {
        throw new Error("transient failure");
      });
    });

    expect(result.current.status).toBe("error");
    expect(result.current.data).toBe("first-good-value");
  });

  it("discards an out-of-order (stale) resolution when a newer run has already completed", async () => {
    // This is the guard that makes "cached response + fresher response can't
    // race" safe: run() #1 starts, then run() #2 starts and finishes first,
    // then run() #1's slow response finally arrives -- the older result
    // must never overwrite the newer one.
    const { result } = renderHook(() => useAsyncResource<string>());

    const first = deferred<string>();
    let firstRunPromise!: Promise<unknown>;
    act(() => {
      firstRunPromise = result.current.run(() => first.promise);
    });

    await act(async () => {
      await result.current.run(async () => "second-fresh-value");
    });
    expect(result.current.data).toBe("second-fresh-value");

    await act(async () => {
      first.resolve("first-stale-value");
      await firstRunPromise;
    });

    expect(result.current.data).toBe("second-fresh-value");
    expect(result.current.status).toBe("success");
  });

  it("reset() returns to idle with no data", async () => {
    const { result } = renderHook(() => useAsyncResource<string>());
    await act(async () => {
      await result.current.run(async () => "value");
    });
    expect(result.current.status).toBe("success");

    act(() => {
      result.current.reset();
    });

    expect(result.current.status).toBe("idle");
    expect(result.current.data).toBeNull();
  });
});
