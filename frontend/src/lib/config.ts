import type { ConfigValues } from "../api";

/** Clears the draft fields that describe the bundled demo rather than this repo.
 *
 * With no config saved, the server seeds a draft from the demo preset. Showing
 * those values as if they were yours means a blind Save writes the demo's
 * agent, model and test commands into your config. Generic settings
 * (base branch, timeout, concurrency, where tests run) and the plumbing
 * fields the form never shows are kept, so a save still round-trips them.
 */
export function withoutDraftValues(config: ConfigValues): ConfigValues {
  return {
    ...config,
    github: { ...config.github, repo: "" },
    agent: { ...config.agent, sbx_agent: "", model: "" },
    run: { ...config.run, test_command: "", lint_command: "" },
    agents: {},
  };
}
