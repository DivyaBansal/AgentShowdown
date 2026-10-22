import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useNow } from "./useNow";

describe("useNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not advance while inactive", () => {
    const { result } = renderHook(() => useNow(false));

    act(() => { vi.advanceTimersByTime(5_000); });

    expect(result.current).toBe(1_000);
  });

  it("catches up straight away when it becomes active", () => {
    const { result, rerender } = renderHook(({ active }) => useNow(active), {
      initialProps: { active: false },
    });

    vi.setSystemTime(60_000);
    rerender({ active: true });
    act(() => { vi.advanceTimersByTime(0); });

    expect(result.current).toBe(60_000);
  });

  it("ticks every interval while active", () => {
    const { result } = renderHook(() => useNow(true, 1_000));

    act(() => { vi.advanceTimersByTime(3_000); });

    expect(result.current).toBe(4_000);
  });
});
