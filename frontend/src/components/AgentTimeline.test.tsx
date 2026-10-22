import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentEvents, TimelineEntry } from "../api";
import { AgentTimeline } from "./AgentTimeline";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function entry(overrides: Partial<TimelineEntry>): TimelineEntry {
  return {
    at: "2026-10-22T10:00:00Z",
    kind: "tool",
    title: "Bash",
    detail: null,
    duration_ms: null,
    ok: true,
    name: "claude_code.tool_result",
    seq: 0,
    ...overrides,
  };
}

const EVENTS: AgentEvents = {
  timeline: [
    entry({ seq: 0, kind: "prompt", title: "Prompt", detail: "1,234 characters", ok: null }),
    entry({
      seq: 1,
      at: "2026-10-22T10:00:05Z",
      kind: "model",
      title: "claude-haiku-4-5",
      detail: "1,200 in · 80 out",
    }),
    entry({
      seq: 2,
      at: "2026-10-22T10:01:07Z",
      title: "Bash",
      detail: "pytest -q",
      duration_ms: 1200,
    }),
    entry({ seq: 3, at: "2026-10-22T10:01:09Z", title: "Edit", detail: "src/app.py", ok: false }),
  ],
  summary: {
    event_counts: {},
    cost_usd: 0.0123,
    tools: {
      Bash: { calls: 1, failures: 0, duration_ms: 1200 },
      Edit: { calls: 1, failures: 1, duration_ms: 5 },
    },
  },
};

function stubFetch(body: AgentEvents) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ...body, events: [] }),
  });

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

describe("AgentTimeline", () => {
  it("lists each tool call with what it ran, in order", async () => {
    stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    const steps = await screen.findByRole("list", { name: /agent steps/i });
    const rows = within(steps).getAllByRole("listitem");

    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("+1:07");
    expect(rows[0]).toHaveTextContent("Bash");
    expect(rows[0]).toHaveTextContent("pytest -q");
    expect(rows[0]).toHaveTextContent("1.2 s");
    expect(rows[1]).toHaveTextContent("src/app.py");
  });

  it("says which tool calls failed in text, not only colour", async () => {
    stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    const steps = await screen.findByRole("list", { name: /agent steps/i });
    expect(within(steps).getByText(/\(failed\)/)).toBeInTheDocument();
    expect(screen.getByText(/2 tool calls, 1 failed · \$0\.0123/)).toBeInTheDocument();
  });

  it("rolls tool calls up per tool", async () => {
    stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    const table = await screen.findByRole("table", { name: /tool calls by tool/i });
    const edit = within(table).getByRole("row", { name: /edit/i });
    expect(within(edit).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "1",
      "1",
      "5 ms",
    ]);
  });

  it("shows model requests and prompts under Everything", async () => {
    stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={false} />);
    await screen.findByRole("list", { name: /agent steps/i });

    fireEvent.click(screen.getByRole("button", { name: "Everything" }));

    expect(screen.getByRole("button", { name: "Everything" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const rows = within(screen.getByRole("list", { name: /agent steps/i })).getAllByRole(
      "listitem",
    );

    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveTextContent("1,200 in · 80 out");
  });

  it("explains an empty timeline for an agent that exports nothing", async () => {
    stubFetch({ timeline: [], summary: { event_counts: {}, cost_usd: null, tools: {} } });
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    expect(await screen.findByText(/only claude and codex export it/i)).toBeInTheDocument();
  });

  it("keeps fetching while the job is live", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={true} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6100);
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/jobs/box-1/agent-events");
  });

  it("fetches once and stops for a finished job", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch(EVENTS);
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("alerts when the timeline can't be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "database is locked" }),
      }),
    );
    render(<AgentTimeline sandboxName="box-1" live={false} />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/database is locked/i),
    );
  });
});
