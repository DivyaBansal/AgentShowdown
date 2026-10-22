import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Sample } from "../api";
import { useSamples } from "./useSamples";

interface Pending {
  url: string;
  resolve: (samples: Sample[]) => void;
}

function stubSamplesFetch(): Pending[] {
  const pending: Pending[] = [];

  vi.stubGlobal("fetch", vi.fn((url: string) => new Promise((resolve) => {
    pending.push({
      url,
      resolve: (samples) => resolve({ ok: true, status: 200, json: async () => ({ samples }) }),
    });
  })));

  return pending;
}

function sample(ts: string): Sample {
  return { sandbox_name: "box", ts, cpu_cores: 1, mem_bytes: 1, mem_limit_bytes: null, pids: 1 };
}

interface Selection {
  name: string | null;
}

function renderSelected(initial: string | null) {
  const initialProps: Selection = { name: initial };

  return renderHook(({ name }: Selection) => useSamples(name), { initialProps });
}

describe("useSamples", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the selected job's samples once they load", async () => {
    const pending = stubSamplesFetch();
    const { result } = renderHook(() => useSamples("box-a"));

    expect(result.current).toEqual([]);
    pending[0]?.resolve([sample("t1")]);

    await waitFor(() => expect(result.current).toEqual([sample("t1")]));
  });

  it("returns nothing once the selection is cleared", async () => {
    const pending = stubSamplesFetch();
    const { result, rerender } = renderSelected("box-a");

    pending[0]?.resolve([sample("t1")]);
    await waitFor(() => expect(result.current).toHaveLength(1));
    rerender({ name: null });

    expect(result.current).toEqual([]);
  });

  it("does not show the previous job's samples while the next one loads", async () => {
    const pending = stubSamplesFetch();
    const { result, rerender } = renderSelected("box-a");

    pending[0]?.resolve([sample("a")]);
    await waitFor(() => expect(result.current).toEqual([sample("a")]));
    rerender({ name: "box-b" });

    expect(result.current).toEqual([]);
  });

  it("drops a late response for a job that is no longer selected", async () => {
    const pending = stubSamplesFetch();
    const { result, rerender } = renderSelected("box-a");

    rerender({ name: "box-b" });
    pending[1]?.resolve([sample("b")]);
    await waitFor(() => expect(result.current).toEqual([sample("b")]));
    pending[0]?.resolve([sample("a")]);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(result.current).toEqual([sample("b")]);
  });
});
