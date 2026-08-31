import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useJobStream } from "./useJobStream";
import { MockEventSource } from "../test/setup";

function jobsResponse(count: number) {
  return {
    ok: true,
    json: async () => ({
      jobs: Array.from({ length: count }, (_, i) => ({
        sandbox_name: `box-${i}`,
        feature_id: "f1",
        agent_id: "claude",
        run_label: null,
        status: "running",
      })),
    }),
  };
}

describe("useJobStream", () => {
  beforeEach(() => {
    MockEventSource.reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads jobs on mount", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jobsResponse(2)));
    const { result } = renderHook(() => useJobStream());
    await waitFor(() => expect(result.current.jobs).toHaveLength(2));
  });

  it("refetches full state when an event arrives", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jobsResponse(1));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useJobStream());
    await waitFor(() => expect(result.current.jobs).toHaveLength(1));

    fetchMock.mockResolvedValue(jobsResponse(3));
    act(() => MockEventSource.last?.emit("job_changed", { sandbox_name: "box-0" }));

    await waitFor(() => expect(result.current.jobs).toHaveLength(3));
  });

  it("reconciles over HTTP on every connect, not only the first", async () => {
    // This is what makes closing the laptop lid safe: even if replay is
    // unavailable, a reconnect converges on the truth.
    const fetchMock = vi.fn().mockResolvedValue(jobsResponse(1));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useJobStream());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const afterMount = fetchMock.mock.calls.length;

    act(() => MockEventSource.last?.emit("open"));

    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(afterMount),
    );
  });

  it("treats a resync as a full refetch", async () => {
    // The server could not replay far enough back; refetching is the
    // documented recovery, and silently missing updates is not an option.
    const fetchMock = vi.fn().mockResolvedValue(jobsResponse(1));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useJobStream());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const before = fetchMock.mock.calls.length;

    act(() => MockEventSource.last?.emit("resync", { reason: "gap" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before),
    );
  });

  it("reports disconnection without dropping the jobs it already has", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jobsResponse(2)));
    const { result } = renderHook(() => useJobStream());
    await waitFor(() => expect(result.current.jobs).toHaveLength(2));

    act(() => MockEventSource.last?.emit("error"));

    expect(result.current.connected).toBe(false);
    // Stale data beats an empty screen while reconnecting.
    expect(result.current.jobs).toHaveLength(2);
  });

  it("closes the stream on unmount so nothing leaks", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jobsResponse(1)));
    const { unmount } = renderHook(() => useJobStream());
    await waitFor(() => expect(MockEventSource.last).toBeDefined());

    const source = MockEventSource.last!;
    unmount();

    expect(source.closed).toBe(true);
  });

  it("surfaces a load failure instead of showing an empty list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: "Down" }),
    );
    const { result } = renderHook(() => useJobStream());
    await waitFor(() => expect(result.current.error).toBeTruthy());
  });
});
