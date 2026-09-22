// The only place network calls to the backend live. Components import typed
// functions from here so error handling and types stay in one place.
//
// Field names are snake_case to mirror the Python models exactly (see the Job
// and Run interfaces below). Translating between cases at the boundary would
// just add a place for names to drift.

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
  /** Has no built-in launcher, so a `command` is the only way to run it. */
  requires_command: boolean;
  /** Command-only agents never receive --model, so don't offer one. */
  accepts_model: boolean;
  /** False where the flag exists in the spec but the CLI ignores it. */
  supports_skip_permissions: boolean;
  verified: boolean;
  known_models: string[];
  default_model: string | null;
  reports_token_usage: boolean;
}

export interface AgentSpec {
  agent_id: string;
  run_label: string | null;
  model: string | null;
  /** Replaces the built-in launcher entirely; required for agent kits that
   *  have none (e.g. "shell"). Runs inside the sandbox. */
  command?: string | null;
  dangerously_skip_permissions?: boolean | null;
  kit?: string[];
  provider?: string | null;
  /** Baked into the sandbox as `sbx create -e`. This is how an agent is
   *  pointed at a local Ollama or a gateway. */
  env?: Record<string, string>;
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
  /** Which repo the job ran against; null for rows recorded before one
   *  shared database held every workspace. */
  repo: string | null;
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

/** One feature and the agents to run it through. */
export interface RunEntry {
  feature_id: string;
  agents: AgentSpec[];
}

export interface StartRunRequest {
  /** Each entry becomes its own run; they execute concurrently. */
  entries: RunEntry[];
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

// Every call here is either quick (a read, or kicking off a job the UI then
// polls for) -- nothing is expected to block on a slow backend operation.
// So one blanket timeout is enough: past this, the backend (or whatever it
// shelled out to) is stuck, not just slow.
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Built conditionally rather than spreading an `undefined` header:
  // exactOptionalPropertyTypes treats an explicit undefined as a real value.
  const options: RequestInit = { ...init };
  if (init?.body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
  }
  const controller = new AbortController();
  options.signal = controller.signal;
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`/api${path}`, options);
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(`Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s`, 0);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
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

export async function fetchJobs(repo?: string): Promise<Job[]> {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  return (await request<{ jobs: Job[] }>(`/jobs${query}`)).jobs;
}

export async function fetchSamples(sandboxName: string): Promise<Sample[]> {
  const path = `/jobs/${encodeURIComponent(sandboxName)}/samples`;
  return (await request<{ samples: Sample[] }>(path)).samples;
}

export async function fetchSandboxes(): Promise<SandboxSummary[]> {
  return (await request<{ sandboxes: SandboxSummary[] }>("/sandboxes")).sandboxes;
}

export async function startRun(body: StartRunRequest): Promise<{ run_ids: string[] }> {
  return request<{ run_ids: string[] }>("/runs", {
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


// --- workspaces, config, features, secrets, issues --------------------------

export interface WorkspaceInfo {
  path: string;
  exists: boolean;
  is_git_repo: boolean;
  origin_url: string | null;
  github_repo: string | null;
  default_branch: string | null;
  has_config: boolean;
  has_features: boolean;
}

export interface WorkspaceEntry {
  path: string;
  origin_url: string | null;
  github_repo: string | null;
  cloned: boolean;
}

export interface WorkspaceList {
  active: string | null;
  workspaces: WorkspaceEntry[];
}

export interface CloneStatus {
  clone_id: string;
  url: string;
  target_path: string;
  state: "running" | "finished" | "failed";
  detail: string;
  github_repo: string | null;
}

export interface GithubConfig {
  repo: string;
  pat_secret_name: string;
  base_branch: string;
  open_pr: boolean;
}

export interface AgentConfig {
  sbx_agent: string;
  model: string;
  dangerously_skip_permissions: boolean;
  max_turns: number | null;
  provider: string | null;
}

export interface RunConfig {
  status_file: string;
  liveness_interval_seconds: number;
  poll_interval_seconds: number;
  timeout_minutes: number;
  max_concurrency: number;
  verify_on: "host" | "sandbox";
  test_command: string;
  lint_command: string;
  telemetry: boolean;
  telemetry_interval_seconds: number;
  capture_usage: boolean;
  remove_sandbox_on_success: boolean;
  remove_sandbox_on_failure: boolean;
}

export interface AgentProfile {
  model: string | null;
  provider: string | null;
  dangerously_skip_permissions: boolean | null;
  kit: string[];
  env: Record<string, string>;
}

export interface ConfigValues {
  github: GithubConfig;
  agent: AgentConfig;
  run: RunConfig;
  agents: Record<string, AgentProfile>;
}

export interface ConfigDoc {
  path: string;
  exists: boolean;
  source: "env" | "workspace" | "demo";
  editable: boolean;
  repo_path: string;
  config: ConfigValues;
}

export interface FeaturesMeta {
  path: string;
  exists: boolean;
  source: "env" | "workspace" | "demo";
  editable: boolean;
}

export interface FeatureInput {
  id: string;
  description: string;
  acceptance_criteria: string[];
  agents: AgentSpec[];
}

export interface SecretEntry {
  scope: string;
  type: string;
  name: string;
  state: string;
}

export interface SecretList {
  available: boolean;
  secrets: SecretEntry[];
}

/** Exactly one of token/ref/command must be set -- the server enforces it.
 *  Prefer ref or command: those store a *reference* sbx resolves on demand,
 *  so the value never passes through this app. */
export interface SecretInput {
  service: string;
  token?: string;
  ref?: string;
  command?: string;
  sandbox?: string;
}

export interface GithubIssue {
  number: number;
  title: string;
  body: string;
  state: string;
  url: string;
  labels: { name: string }[];
}

export function fetchWorkspaces(): Promise<WorkspaceList> {
  return request<WorkspaceList>("/workspaces");
}

export function inspectWorkspace(path: string): Promise<WorkspaceInfo> {
  return request<WorkspaceInfo>("/workspaces/inspect", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function selectWorkspace(path: string): Promise<WorkspaceEntry> {
  return request<WorkspaceEntry>("/workspaces/select", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function cloneRepo(url: string, directoryName?: string): Promise<CloneStatus> {
  // Built conditionally rather than spread: exactOptionalPropertyTypes means
  // an explicit `undefined` is a real value and would fail validation.
  const body: { url: string; directory_name?: string } = { url };
  if (directoryName !== undefined && directoryName !== "") {
    body.directory_name = directoryName;
  }
  return request<CloneStatus>("/workspaces/clone", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchCloneStatus(cloneId: string): Promise<CloneStatus> {
  return request<CloneStatus>(`/workspaces/clone/${encodeURIComponent(cloneId)}`);
}

export function fetchConfig(): Promise<ConfigDoc> {
  return request<ConfigDoc>("/config");
}

export function saveConfig(config: ConfigValues): Promise<{ path: string }> {
  return request<{ path: string }>("/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export function fetchFeaturesMeta(): Promise<FeaturesMeta> {
  return request<FeaturesMeta>("/features/meta");
}

export function saveFeatures(features: FeatureInput[]): Promise<{ count: number }> {
  return request<{ count: number }>("/features", {
    method: "PUT",
    body: JSON.stringify({ features }),
  });
}

export function fetchSecrets(): Promise<SecretList> {
  return request<SecretList>("/secrets");
}

export function storeSecret(input: SecretInput): Promise<{ service: string; method: string }> {
  return request<{ service: string; method: string }>("/secrets", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function fetchIssues(repo?: string): Promise<{ repo: string; issues: GithubIssue[] }> {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  return request<{ repo: string; issues: GithubIssue[] }>(`/github/issues${query}`);
}

export function importIssues(
  issues: number[],
  agents: { agent_id: string }[] = [],
): Promise<{ imported: number }> {
  return request<{ imported: number }>("/features/import-issues", {
    method: "POST",
    body: JSON.stringify({ issues, agents }),
  });
}
