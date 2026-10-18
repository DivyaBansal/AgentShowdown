import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchInit, jsonBody, type Json } from "./test/fetch";
import {
  ApiError,
  answerJob,
  fetchAgents,
  fetchFeatures,
  fetchJobLog,
  fetchPreflight,
  fetchRun,
  fetchRuns,
  fetchSamples,
  fetchSandboxes,
  killAllSandboxes,
  openEventStream,
  pingSandbox,
  removeSandbox,
  respawnJob,
  startRun,
  stopSandbox,
} from "./api";

afterEach(() => vi.restoreAllMocks());

function ok(body: Json) {
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
      fetchInit(fetchMock, 0)?.headers,
    ).toBeUndefined();

    await answerJob("box-1", "use the second approach");
    expect(
      fetchInit(fetchMock, 1)?.headers,
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

describe("fetchJobLog", () => {
  it("returns the tail text, unwrapped from the envelope", async () => {
    vi.stubGlobal("fetch", ok({ sandbox_name: "box-1", tail: "building...\ndone." }));
    await expect(fetchJobLog("box-1")).resolves.toBe("building...\ndone.");
  });

  it("omits the query string when no tail length is given", async () => {
    const fetchMock = ok({ sandbox_name: "box-1", tail: "" });
    vi.stubGlobal("fetch", fetchMock);
    await fetchJobLog("box-1");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/jobs/box-1/log");
  });

  it("passes a custom tail length through as a query param", async () => {
    const fetchMock = ok({ sandbox_name: "box-1", tail: "" });
    vi.stubGlobal("fetch", fetchMock);
    await fetchJobLog("box-1", 50);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/jobs/box-1/log?tail=50");
  });
});

describe("mutations", () => {
  it("pings a sandbox", async () => {
    vi.stubGlobal("fetch", ok({ sandbox_name: "box-1", reachable: true }));
    await expect(pingSandbox("box-1")).resolves.toMatchObject({ reachable: true });
  });

  it("removes one sandbox, defaulting force to false", async () => {
    const fetchMock = ok({ removed: ["box-1"] });
    vi.stubGlobal("fetch", fetchMock);
    await expect(removeSandbox("box-1")).resolves.toEqual({ removed: ["box-1"] });
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ force: false });
  });

  it("removes a sandbox with force when the safeguard should be bypassed", async () => {
    const fetchMock = ok({ removed: ["box-1"] });
    vi.stubGlobal("fetch", fetchMock);
    await removeSandbox("box-1", true);
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ force: true });
  });

  it("stops a sandbox", async () => {
    vi.stubGlobal("fetch", ok({ stopped: "box-1" }));
    await expect(stopSandbox("box-1")).resolves.toEqual({ stopped: "box-1" });
  });

  it("respawns a job", async () => {
    vi.stubGlobal("fetch", ok({ status: "respawning", sandbox_name: "box-1" }));
    await expect(respawnJob("box-1")).resolves.toEqual({
      status: "respawning",
      sandbox_name: "box-1",
    });
  });

  it("sends the exact confirmation phrase when wiping every sandbox", async () => {
    const fetchMock = ok({ removed: [], jobs_marked_lost: 0 });
    vi.stubGlobal("fetch", fetchMock);
    await killAllSandboxes();
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ confirm: "KILL ALL" });
  });
});

describe("openEventStream", () => {
  it("subscribes through the same /api prefix", () => {
    expect(openEventStream().url).toBe("/api/events");
  });
});

describe("ApiError", () => {
  it("carries its status", () => {
    const error = new ApiError("nope", 503);
    expect(error.status).toBe(503);
    expect(error.name).toBe("ApiError");
  });
});
