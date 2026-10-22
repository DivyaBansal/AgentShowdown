/** One model to put a quick question to: a provider and a model name.
 *
 * The model is free text backed by a datalist, the same idiom as
 * `AgentSpecFields`: suggestions come from the backend (an account's codex
 * models, what Ollama has pulled), but any name the provider serves works.
 * Providers this host can't use stay listed, disabled, so it's clear they
 * exist and the hint says what would enable them.
 */

import type { AskContender, AskProviderInfo } from "../api";
import { defaultModelFor } from "../lib/ask";
import { modelHint } from "../lib/modelCatalog";
import { Field } from "./ui/Field";

export function AskContenderRow({
  contender,
  index,
  providers,
  canRemove,
  onChange,
  onRemove,
}: {
  contender: AskContender;
  index: number;
  providers: AskProviderInfo[];
  canRemove: boolean;
  onChange: (next: AskContender) => void;
  onRemove: () => void;
}) {
  const info = providers.find((p) => p.provider === contender.provider);
  const providerId = `ask-provider-${index}`;
  const modelId = `ask-model-${index}`;
  const listId = `ask-models-${index}-${info?.models_updated_at ?? "builtin"}`;

  return (
    <fieldset className="group group--framed ask-contender">
      <legend>Model {index + 1}</legend>
      <div className="field-grid">
        <Field id={providerId} label="Provider" hint={info?.hint}>
          <select
            id={providerId}
            value={contender.provider}
            onChange={(e) => {
              const next = providers.find((p) => p.provider === e.target.value);

              if (next) onChange({ provider: next.provider, model: defaultModelFor(next) });
            }}
          >
            {providers.map((p) => (
              <option key={p.provider} value={p.provider} disabled={!p.configured}>
                {p.configured ? p.label : `${p.label} (not set up)`}
              </option>
            ))}
          </select>
        </Field>
        <Field id={modelId} label="Model"
          hint={info?.provider === "openrouter" ? undefined
            : modelHint(contender.model, info?.suggested_models ?? [], info?.model_source)}>
          <input
            id={modelId}
            list={listId}
            value={contender.model}
            onChange={(e) => onChange({ ...contender, model: e.target.value })}
            required
            autoComplete="off"
            spellCheck={false}
          />
          <datalist key={listId} id={listId}>
            {(info?.suggested_models ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
      </div>
      <div className="actions">
        <button type="button" className="btn--quiet" disabled={!canRemove} onClick={onRemove}>
          Remove model {index + 1}
        </button>
      </div>
    </fieldset>
  );
}
