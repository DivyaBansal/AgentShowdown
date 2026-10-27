import type { EnvVars } from "../api";

/** Pointing an agent at the host's Ollama, as a preset of env vars.
 *
 * Nothing here is new capability: the backend already routes an agent to a
 * custom endpoint through its `env` (and waives the known-model check when a
 * base-URL var is set). This just writes the variables each agent needs so
 * nobody has to remember them.
 */

/** How a sandbox reaches the host. Ollama only listens on the host's
 *  loopback, and sbx forwards this name to it. */
export const OLLAMA_BASE_URL = "http://host.docker.internal:11434";

/** Ollama ignores the token but claude-code refuses to start without one.
 *  A placeholder, not a secret. */
const OLLAMA_AUTH_TOKEN = "ollama";

const BASE_URL_KEY = "ANTHROPIC_BASE_URL";
const AUTH_TOKEN_KEY = "ANTHROPIC_AUTH_TOKEN";

/** claude-code makes background calls with its "haiku" model; Ollama serves
 *  no such name, so those go to the chosen model too. */
const HAIKU_MODEL_KEY = "ANTHROPIC_DEFAULT_HAIKU_MODEL";

/** codex's own variable for its `--oss` provider. Setting it is also what
 *  makes the backend run codex with `--oss --local-provider ollama`. */
const CODEX_OSS_BASE_URL_KEY = "CODEX_OSS_BASE_URL";

/** codex speaks Ollama's OpenAI-compatible API, which lives under /v1. */
const CODEX_OLLAMA_BASE_URL = `${OLLAMA_BASE_URL}/v1`;

const OLLAMA_KEYS = [BASE_URL_KEY, AUTH_TOKEN_KEY, HAIKU_MODEL_KEY, CODEX_OSS_BASE_URL_KEY];

/** The agents this preset applies to: those with an Ollama route wired up. */
export function supportsOllamaPreset(agentId: string): boolean {
  return agentId === "claude" || agentId === "codex";
}

export function usesOllama(agentId: string, env: EnvVars | undefined): boolean {
  if (agentId === "codex") return env?.[CODEX_OSS_BASE_URL_KEY] === CODEX_OLLAMA_BASE_URL;

  return env?.[BASE_URL_KEY] === OLLAMA_BASE_URL;
}

/** `env` with the Ollama variables set, keeping any others the user added. */
export function withOllama(
  agentId: string,
  env: EnvVars | undefined,
  model: string | null,
): EnvVars {
  if (agentId === "codex") {
    return { ...env, [CODEX_OSS_BASE_URL_KEY]: CODEX_OLLAMA_BASE_URL };
  }

  const next: EnvVars = {
    ...env,
    [BASE_URL_KEY]: OLLAMA_BASE_URL,
    [AUTH_TOKEN_KEY]: OLLAMA_AUTH_TOKEN,
  };

  if (model) {
    next[HAIKU_MODEL_KEY] = model;
  } else {
    delete next[HAIKU_MODEL_KEY];
  }

  return next;
}

/** `env` with the Ollama variables removed, keeping any others. */
export function withoutOllama(env: EnvVars | undefined): EnvVars {
  return Object.fromEntries(
    Object.entries(env ?? {}).filter(([key]) => !OLLAMA_KEYS.includes(key)),
  );
}
