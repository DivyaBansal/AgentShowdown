/** Model defaults for the Ask view's contender rows. */

import type { AskProviderId, AskProviderInfo } from "../api";

/** Sensible first picks per provider; else whatever the backend suggests first. */
const PREFERRED_MODELS: Partial<Record<AskProviderId, string>> = {
  claude: "claude-sonnet-5-5",
  codex: "gpt-5.6-sol",
};

export function defaultModelFor(info: AskProviderInfo): string {
  const preferred = PREFERRED_MODELS[info.provider];

  if (preferred && info.suggested_models.includes(preferred)) return preferred;

  return info.suggested_models[0] ?? "";
}
