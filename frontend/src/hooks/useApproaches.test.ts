import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Approach } from "../api";
import { useApproaches } from "./useApproaches";

const EMPTY: Approach = {
  phases: [],
  steps: [],
  stats: {
    calls: 0,
    failures: 0,
    phase_calls: { explore: 0, edit: 0, verify: 0, setup: 0, git: 0, web: 0, delegate: 0, other: 0 },
    phase_ms: { explore: 0, edit: 0, verify: 0, setup: 0, git: 0, web: 0, delegate: 0, other: 0 },
    calls_before_first_edit: null,
    seconds_to_first_edit: null,
    seconds_to_first_verify: null,
    edit_verify_cycles: 0,
    files_read: [],
    files_edited: [],
    span_s: null,
  },
};

/** What a misrouted request answers with: some other endpoint's body. */
interface WrongBody {
  jobs: never[];
}

function stubFetch(body: Approach | WrongBody) {
  const fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => body }));

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useApproaches", () => {
  it("returns each named job's approach", async () => {
    stubFetch(EMPTY);
    const { result } = renderHook(() => useApproaches(["a", "b"], false));

    await waitFor(() => expect(result.current.size).toBe(2));
    expect(result.current.get("a")).toEqual({ ok: true, approach: EMPTY });
  });

  it("treats a response that isn't an approach as an error, not data", async () => {
    stubFetch({ jobs: [] });
    const { result } = renderHook(() => useApproaches(["a"], false));

    await waitFor(() => expect(result.current.get("a")).toEqual({ ok: false, error: "Unexpected response from the server" }));
  });

  it("keeps polling while live and stops once the jobs finish", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch(EMPTY);

    const { rerender } = renderHook(({ live }: { live: boolean }) => useApproaches(["a"], live), {
      initialProps: { live: true },
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6500);
    });
    const whileLive = fetchMock.mock.calls.length;

    expect(whileLive).toBeGreaterThanOrEqual(3);

    rerender({ live: false });
    // One last read when it stops, for the batch an agent flushes on exit.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(fetchMock.mock.calls.length).toBe(whileLive + 1);
  });

  it("drops jobs that are no longer asked for", async () => {
    stubFetch(EMPTY);

    const { result, rerender } = renderHook(({ names }: { names: string[] }) => useApproaches(names, false), {
      initialProps: { names: ["a"] },
    });

    await waitFor(() => expect(result.current.has("a")).toBe(true));
    rerender({ names: ["b"] });

    expect(result.current.has("a")).toBe(false);
  });
});
