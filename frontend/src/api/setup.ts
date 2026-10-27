// Setup: preflight, agents, features, workspaces, config, secrets, issues.
// Field names are snake_case to mirror the Python models exactly. Translating
// between cases at the boundary would just add a place for names to drift.

import { request } from "./client";

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
  model_source?: string;
  models_updated_at?: string | null;
  default_model: string | null;
  reports_token_usage: boolean;
}

/** Environment variables passed to an agent's sandbox (`sbx create -e`). */
export type EnvVars = Record<string, string>;

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
  env?: EnvVars;
}

export interface Feature {
  id: string;
  description: string;
  acceptance_criteria: string[];
  agents: AgentSpec[];
}

/** Where the test/lint commands run after an agent finishes. */
export type VerifyOn = "host" | "sandbox";

/** Reads a `<select>` value back into a `VerifyOn`; anything unexpected is `host`. */
export function parseVerifyOn(value: string): VerifyOn {
  return value === "sandbox" ? "sandbox" : "host";
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
  verify_on: VerifyOn;
  test_command: string;
  lint_command: string;
  telemetry: boolean;
  telemetry_interval_seconds: number;
  capture_usage: boolean;
  // Where sandboxed claude/codex send OTel events; null turns export off.
  agent_otel_endpoint: string | null;
  remove_sandbox_on_success: boolean;
  remove_sandbox_on_failure: boolean;
}

export interface AgentProfile {
  model: string | null;
  provider: string | null;
  dangerously_skip_permissions: boolean | null;
  kit: string[];
  env: EnvVars;
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

/** Deselects the active repo; it stays in the known list. */
export function clearWorkspace(): Promise<{ active: null; previous: string | null }> {
  return request<{ active: null; previous: string | null }>("/workspaces/active", {
    method: "DELETE",
  });
}

/** Drops a repo from the known list; its files stay on disk. */
export function forgetWorkspace(path: string): Promise<{ forgotten: string }> {
  return request<{ forgotten: string }>("/workspaces/forget", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

interface CloneRequest {
  url: string;
  directory_name?: string;
}

export function cloneRepo(url: string, directoryName?: string): Promise<CloneStatus> {
  // Built conditionally rather than spread: exactOptionalPropertyTypes means
  // an explicit `undefined` is a real value and would fail validation.
  const body: CloneRequest = { url };

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
