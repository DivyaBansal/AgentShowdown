// Quick questions: one prompt answered side by side by several providers.

import { request } from "./client";

export type AskProviderId = "claude" | "codex" | "openrouter" | "ollama";

export interface AskProviderInfo {
  provider: AskProviderId;
  label: string;
  /** Whether this host can use it right now (CLI installed, key set, …). */
  configured: boolean;
  hint: string;
  suggested_models: string[];
  model_source?: string;
  models_updated_at?: string | null;
}

export interface AskContender {
  provider: AskProviderId;
  model: string;
}

export interface AskAnswer extends AskContender {
  status: "pending" | "done" | "error";
  text: string;
  error: string | null;
  latency_ms: number | null;
  /** null means "not reported", never zero. */
  input_tokens: number | null;
  output_tokens: number | null;
}

export interface AskResult {
  ask_id: string;
  question: string;
  created_at: number;
  answers: AskAnswer[];
}

export async function fetchAskProviders(): Promise<AskProviderInfo[]> {
  return (await request<{ providers: AskProviderInfo[] }>("/ask/providers")).providers;
}

export function startAsk(question: string, contenders: AskContender[]): Promise<{ ask_id: string }> {
  return request<{ ask_id: string }>("/asks", {
    method: "POST",
    body: JSON.stringify({ question, contenders }),
  });
}

export function fetchAsk(askId: string): Promise<AskResult> {
  return request<AskResult>(`/asks/${encodeURIComponent(askId)}`);
}
