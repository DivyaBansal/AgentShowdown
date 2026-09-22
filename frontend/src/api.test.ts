import { describe, it, expect, vi, afterEach } from "vitest";
import {
  ApiError,
  answerJob,
  fetchAgents,
  fetchFeatures,
  fetchPreflight,
  fetchRun,
  fetchRuns,
  fetchSamples,
  fetchSandboxes,
  killAllSandboxes,
  openEventStream,
  pingSandbox,
  removeSandbox,
  startRun,
} from "./api";

afterEach(() => vi.restoreAllMocks());

function ok(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

describe("request handling", () => {
  it("prefixes every call with /api so the dev proxy picks it up", async () => {
    const fetchMock = ok({ status: "ok" });
    vi.stubGlobal("fetch", fetchMock);
    await fetchPreflight();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/preflight");
  });

  it("sets a JSON content type only when there is a body", async () => {
    const fetchMock = ok({ runs: [] });
    vi.stubGlobal("fetch", fetchMock);

    await fetchRuns();
    expect(
      (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.headers,
    ).toBeUndefined();

    await answerJob("box-1", "use the second approach");
    expect(
      (fetchMock.mock.calls[1]?.[1] as RequestInit | undefined)?.headers,
    ).toEqual({ "Content-Type": "application/json" });
  });

  it("surfaces the server's detail message on an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        json: async () => ({ detail: "Unknown feature id: nope" }),
      }),
    );
    await expect(startRun({ entries: [{ feature_id: "nope", agents: [] }] })).rejects.toThrow(
      /Unknown feature id/,
    );
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    // A proxy 502 has no FastAPI detail field; the user still needs a reason.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => {
          throw new Error("not json");
        },
      }),
    );
    await expect(fetchPreflight()).rejects.toThrow(/Bad Gateway/);
  });

  it("reports the status code on the error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: async () => ({}),
      }),
    );
    await expect(fetchRun("run-nope")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });

  it("gives up and reports a timeout when the server never responds", async () => {
    vi.useFakeTimers();
    // A realistic stand-in for fetch: it never settles on its own, but
    // (like the real thing) rejects once its AbortSignal fires.
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        });
      }),
    );

    const pending = expect(fetchPreflight()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
    });
    await vi.advanceTimersByTimeAsync(20_000);
    await pending;

    vi.useRealTimers();
  });

  it("escapes identifiers that would otherwise break the path", async () => {
    const fetchMock = ok({ samples: [] });
    vi.stubGlobal("fetch", fetchMock);
    await fetchSamples("arena/f1 claude");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/jobs/arena%2Ff1%20claude/samples",
    );
  });
});

describe("endpoints unwrap their envelope", () => {
  it.each([
    ["agents", fetchAgents, { agents: [{ agent_id: "claude" }] }],
    ["features", fetchFeatures, { features: [{ id: "f1" }] }],
    ["runs", fetchRuns, { runs: [{ run_id: "run-1" }] }],
    ["sandboxes", fetchSandboxes, { sandboxes: [{ name: "box-1" }] }],
    ["samples", () => fetchSamples("box-1"), { samples: [{ ts: "t" }] }],
  ])("%s", async (_name, call, body) => {
    vi.stubGlobal("fetch", ok(body));
    await expect(call()).resolves.toHaveLength(1);
  });
});

describe("mutations", () => {
  it("pings a sandbox", async () => {
    vi.stubGlobal("fetch", ok({ sandbox_name: "box-1", reachable: true }));
    await expect(pingSandbox("box-1")).resolves.toMatchObject({ reachable: true });
  });

  it("removes one sandbox", async () => {
    vi.stubGlobal("fetch", ok({ removed: ["box-1"] }));
    await expect(removeSandbox("box-1")).resolves.toEqual({ removed: ["box-1"] });
  });

  it("sends the exact confirmation phrase when wiping every sandbox", async () => {
    const fetchMock = ok({ removed: [], jobs_marked_lost: 0 });
    vi.stubGlobal("fetch", fetchMock);
    await killAllSandboxes();
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(JSON.parse(String(init?.body))).toEqual({ confirm: "KILL ALL" });
  });
});

describe("openEventStream", () => {
  it("subscribes through the same /api prefix", () => {
    const source = openEventStream() as unknown as { url: string };
    expect(source.url).toBe("/api/events");
  });
});

describe("ApiError", () => {
  it("carries its status", () => {
    const error = new ApiError("nope", 503);
    expect(error.status).toBe(503);
    expect(error.name).toBe("ApiError");
  });
});
