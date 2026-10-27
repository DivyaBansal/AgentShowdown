// Model suggestions: the host's Ollama models and the refreshable catalogs.

import { request } from "./client";

export interface OllamaModels {
  /** False when the host has no Ollama or it isn't answering. */
  available: boolean;
  models: string[];
  model_source?: string;
  models_updated_at?: string | null;
}

/** Models pulled into the host's Ollama, offered as model suggestions. */
export async function fetchOllamaModels(): Promise<OllamaModels> {
  return request<OllamaModels>("/ollama/models");
}

export interface ModelCatalog {
  models: string[];
  source: string;
  updated_at: string | null;
}

export interface ModelRefresh {
  refresh_id: string;
  state: "running" | "finished";
  started_at: string;
  finished_at: string | null;
  error: string | null;
  results: Record<string, {
    status: "updated" | "failed" | "timed_out";
    count?: number;
    source?: string;
    detail: string;
  }>;
}

export interface ModelCatalogState {
  catalogs: Record<string, ModelCatalog>;
  refresh: ModelRefresh | null;
}

export function fetchModelCatalogs(): Promise<ModelCatalogState> {
  return request("/models");
}

export function updateModels(): Promise<ModelRefresh> {
  return request("/models/refresh", { method: "POST" });
}

export function fetchModelRefresh(id: string): Promise<ModelRefresh> {
  return request(`/models/refresh/${encodeURIComponent(id)}`);
}
