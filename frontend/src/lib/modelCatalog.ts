/** Catalogs suggest names; the launched CLI checks actual access. */
export function modelSourceLabel(source?: string): string {
  switch (source) {
    case "cli":
    case "api":
      return "Updated suggestions";
    case "cache":
      return "Cached suggestions";
    case "cli_suggestions":
      return "CLI suggestions (access not verified)";
    default:
      return "Built-in suggestions";
  }
}

export function modelHint(model: string, models: string[], source?: string): string {
  const label = modelSourceLabel(source);

  return model && !models.includes(model)
    ? `${label}. This model is not listed; the agent will check access when launched.`
    : `${label}. You can also enter a model name manually.`;
}
