// The only place network calls to the backend live. Components import typed
// functions from here so error handling and types stay in one place.
//
// Field names are snake_case to mirror the Python models exactly -- the same
// convention the boilerplate's Order/OrderResponse already used. Translating
// between cases at the boundary would just add a place for names to drift.

export interface Order {
  item_id: string;
  quantity: number;
}

export interface OrderResponse {
  status: string;
  item_id: string;
}

export interface PreflightCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Preflight {
  live_runs_possible: boolean;
  checks: PreflightCheck[];
}

export interface AgentInfo {
  agent_id: string;
  has_native_builder: boolean;
  verified: boolean;
  known_models: string[];
  default_model: string | null;
  reports_token_usage: boolean;
}

export interface AgentSpec {
  agent_id: string;
  run_label: string | null;
  model: string | null;
}

export interface Feature {
  id: string;
  description: string;
  acceptance_criteria: string[];
  agents: AgentSpec[];
}

/** A job row. Metric fields are null when the agent never reported them --
 *  which must render as "no data", never as zero. */
export interface Job {
  sandbox_name: string;
  feature_id: string;
  agent_id: string;
  branch: string | null;
  status: string;
  pr_url: string | null;
  detail: string | null;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  model: string | null;
  run_label: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  tests_passed: number | null;
  lint_passed: number | null;
  files_changed: number | null;
  lines_added: number | null;
  lines_removed: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
}

export interface Run {
  run_id: string;
  feature_id: string;
  created_at: string;
  finished_at: string | null;
  mode: string;
  status: string;
}

export interface Sample {
  sandbox_name: string;
  ts: string;
  cpu_cores: number | null;
  mem_bytes: number | null;
  mem_limit_bytes: number | null;
  pids: number | null;
}

export interface SandboxSummary {
  name: string;
  agent?: string;
  status?: string;
}

export interface PingResult {
  sandbox_name: string;
  listed: boolean;
  reachable: boolean;
  latency_ms: number | null;
  agent_alive: boolean | null;
  status_file_present: boolean | null;
}

export interface StartRunRequest {
  feature_id: string;
  agents: { agent_id: string; run_label?: string | null; model?: string | null }[];
  telemetry?: boolean;
  verify_on?: "host" | "sandbox";
  open_pr?: boolean;
  poll_interval_seconds?: number;
  cpus?: number;
  memory?: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Built conditionally rather than spreading an `undefined` header:
  // exactOptionalPropertyTypes treats an explicit undefined as a real value.
  const options: RequestInit = { ...init };
  if (init?.body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(`/api${path}`, options);
  if (!res.ok) {
    // FastAPI puts the useful part in `detail`; fall back to the status text
    // so a proxy error or a 502 still says something.
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body -- statusText is the best we have.
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export async function createOrder(order: Order): Promise<OrderResponse> {
  return request<OrderResponse>("/orders", {
    method: "POST",
    body: JSON.stringify(order),
  });
}

export async function fetchPreflight(): Promise<Preflight> {
  return request<Preflight>("/preflight");
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  return (await request<{ agents: AgentInfo[] }>("/agents")).agents;
}

export async function fetchFeatures(): Promise<Feature[]> {
  return (await request<{ features: Feature[] }>("/features")).features;
}

export async function fetchRuns(): Promise<Run[]> {
  return (await request<{ runs: Run[] }>("/runs")).runs;
}

export async function fetchRun(runId: string): Promise<{ run: Run; jobs: Job[] }> {
  return request<{ run: Run; jobs: Job[] }>(`/runs/${encodeURIComponent(runId)}`);
}

export async function fetchJobs(): Promise<Job[]> {
  return (await request<{ jobs: Job[] }>("/jobs")).jobs;
}

export async function fetchSamples(sandboxName: string): Promise<Sample[]> {
  const path = `/jobs/${encodeURIComponent(sandboxName)}/samples`;
  return (await request<{ samples: Sample[] }>(path)).samples;
}

export async function fetchSandboxes(): Promise<SandboxSummary[]> {
  return (await request<{ sandboxes: SandboxSummary[] }>("/sandboxes")).sandboxes;
}

export async function startRun(body: StartRunRequest): Promise<{ run_id: string }> {
  return request<{ run_id: string }>("/runs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function answerJob(
  sandboxName: string,
  answer: string,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/jobs/${encodeURIComponent(sandboxName)}/answer`,
    { method: "POST", body: JSON.stringify({ answer }) },
  );
}

export async function pingSandbox(sandboxName: string): Promise<PingResult> {
  return request<PingResult>(
    `/sandboxes/${encodeURIComponent(sandboxName)}/ping`,
    { method: "POST" },
  );
}

export async function removeSandbox(
  sandboxName: string,
): Promise<{ removed: string[] }> {
  return request<{ removed: string[] }>(
    `/sandboxes/${encodeURIComponent(sandboxName)}/rm`,
    { method: "POST" },
  );
}

/** Removes every sandbox on the host. The confirmation phrase is checked
 *  server-side too; passing it here is not what makes this safe. */
export async function killAllSandboxes(): Promise<{
  removed: string[];
  jobs_marked_lost: number;
}> {
  return request<{ removed: string[]; jobs_marked_lost: number }>(
    "/sandboxes/kill-all",
    { method: "POST", body: JSON.stringify({ confirm: "KILL ALL" }) },
  );
}

/** Opens the live event stream.
 *
 *  EventSource belongs here with the fetch calls: it is a backend network
 *  call, and centralising them is the point of this module. The browser
 *  resends Last-Event-ID on reconnect by itself, so callers get replay for
 *  free -- but they must still reconcile over HTTP on connect, because a
 *  gap too large to replay arrives as a `resync` event instead. */
export function openEventStream(): EventSource {
  return new EventSource("/api/events");
}
