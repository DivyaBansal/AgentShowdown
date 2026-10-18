/** Pointing the claude agent at the host's Ollama, as a preset of env vars.
 *
 * Nothing here is new capability: the backend already routes an agent to a
 * custom endpoint through its `env` (and waives the known-model check when a
 * base-URL var is set). This just writes the three variables that takes so
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

const OLLAMA_KEYS = [BASE_URL_KEY, AUTH_TOKEN_KEY, HAIKU_MODEL_KEY];

/** The agents this preset applies to: it sets Anthropic-API variables. */
export function supportsOllamaPreset(agentId: string): boolean {
  return agentId === "claude";
}

export function usesOllama(env: Record<string, string> | undefined): boolean {
  return env?.[BASE_URL_KEY] === OLLAMA_BASE_URL;
}

/** `env` with the Ollama variables set, keeping any others the user added. */
export function withOllama(
  env: Record<string, string> | undefined,
  model: string | null,
): Record<string, string> {
  const next: Record<string, string> = {
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
export function withoutOllama(env: Record<string, string> | undefined): Record<string, string> {
  return Object.fromEntries(
    Object.entries(env ?? {}).filter(([key]) => !OLLAMA_KEYS.includes(key)),
  );
}
