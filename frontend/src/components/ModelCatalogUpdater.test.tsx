import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModelCatalogState, ModelRefresh } from "../api";
import { ModelCatalogUpdater } from "./ModelCatalogUpdater";

const running: ModelRefresh = {
  refresh_id: "refresh-1", state: "running", started_at: "2026-10-18T12:00:00Z",
  finished_at: null, error: null, results: {},
};

function stub() {
  const metadata: ModelCatalogState = {
    catalogs: {}, refresh: null,
  };

  const api = {
    metadata,
    progress: running,
    postStatus: 202,
    progressStatus: 200,
    fetch: vi.fn(),
  };

  api.fetch.mockImplementation((url: string, init?: RequestInit) => {
    let status = 200;
    let body: unknown = api.metadata;

    if (url === "/api/models/refresh" && init?.method === "POST") {
      status = api.postStatus;
      body = status === 202 ? running : { detail: "Read-only preset" };
    } else if (url.startsWith("/api/models/refresh/")) {
      status = api.progressStatus;
      body = status === 200 ? api.progress : { detail: "Update lost after restart" };
    }

    return Promise.resolve({ ok: status < 400, status, json: async () => body });
  });
  vi.stubGlobal("fetch", api.fetch);

  return api;
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("ModelCatalogUpdater", () => {
  it("reads metadata without starting discovery and disables updates for read-only config", async () => {
    const api = stub();
    render(<ModelCatalogUpdater disabled />);
    expect(screen.getByRole("button", { name: "Update models" })).toBeDisabled();
    await waitFor(() => expect(api.fetch).toHaveBeenCalledOnce());
    expect(api.fetch.mock.calls[0]?.[0]).toBe("/api/models");
  });

  it("updates only on click, polls, and reports partial failures", async () => {
    const api = stub();
    const onUpdated = vi.fn();
    render(<ModelCatalogUpdater disabled={false} onUpdated={onUpdated} />);
    await waitFor(() => expect(api.fetch).toHaveBeenCalledOnce());
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Update models" })); });
    expect(api.fetch.mock.calls.filter(([url]) => url === "/api/models/refresh")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Update models" })).toBeDisabled();
    expect(screen.getByText(/maximum 10 minutes/)).toBeInTheDocument();

    api.progress = { ...running, state: "finished", finished_at: "2026-10-18T12:02:00Z",
      results: {
        codex: { status: "updated", count: 2, source: "cli", detail: "Updated" },
        claude: { status: "failed", detail: "No API key; retaining previous suggestions" },
      },
    };
    api.metadata = { refresh: api.progress, catalogs: {
      codex: { models: ["new-model"], source: "cli", updated_at: "2026-10-18T12:02:00Z" },
    } };
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(onUpdated).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Update models" })).toBeEnabled();
    expect(screen.getByText(/2 models/)).toBeInTheDocument();
    expect(screen.getByText(/No API key/)).toBeInTheDocument();
    expect(screen.getByText(/Last updated:/)).toBeInTheDocument();

    const calls = api.fetch.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.fetch).toHaveBeenCalledTimes(calls);
  });

  it("reattaches to an in-progress update without starting another", async () => {
    const api = stub();
    api.metadata.refresh = running;
    render(<ModelCatalogUpdater disabled={false} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Update models" })).toBeDisabled());
    expect(api.fetch.mock.calls.some(([url]) => url === "/api/models/refresh")).toBe(false);
  });

  it("recovers after a backend restart loses the refresh ID", async () => {
    const api = stub();
    api.progressStatus = 404;
    render(<ModelCatalogUpdater disabled={false} />);
    await waitFor(() => expect(api.fetch).toHaveBeenCalledOnce());
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Update models" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("alert")).toHaveTextContent(/Update lost/);
    expect(screen.getByRole("button", { name: "Update models" })).toBeEnabled();
  });

  it("shows start failures and allows retry", async () => {
    const api = stub();
    api.postStatus = 409;
    render(<ModelCatalogUpdater disabled={false} />);
    await waitFor(() => expect(api.fetch).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Update models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Read-only preset");
    expect(screen.getByRole("button", { name: "Update models" })).toBeEnabled();
  });
});
