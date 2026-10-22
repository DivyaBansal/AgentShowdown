/** Every contender's answer to one question, side by side.
 *
 * One card per contender, in the order they were asked, so the columns line
 * up with the form. Answers render as plain text with line breaks kept --
 * a markdown renderer would be a new dependency for little gain when the
 * point is comparing content. Missing token counts show as "—", never 0:
 * a false zero would make one model look free next to another.
 */

import { useState } from "react";
import type { AskAnswer, AskProviderInfo, AskResult } from "../api";
import { Notice } from "./ui/Notice";
import { StatusChip, type Tone } from "./ui/StatusChip";

const STATUS: Record<AskAnswer["status"], { tone: Tone; text: string }> = {
  pending: { tone: "active", text: "Thinking" },
  done: { tone: "ok", text: "Answered" },
  error: { tone: "danger", text: "Failed" },
};

function formatSeconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(n: number | null): string {
  return n === null ? "—" : n.toLocaleString();
}

function AnswerCard({ answer, label }: { answer: AskAnswer; label: string }) {
  const [copied, setCopied] = useState(false);
  const status = STATUS[answer.status];
  const name = `${label} · ${answer.model}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(answer.text);
      setCopied(true);
    } catch {
      // No clipboard access (insecure context, denied permission) -- the
      // text is still selectable by hand.
    }
  }

  return (
    <article className="answer-card" aria-label={name}>
      <header className="answer-card__header">
        <div className="answer-card__title">
          <h3>{label}</h3>
          <span className="answer-card__model">{answer.model}</span>
        </div>
        <StatusChip tone={status.tone}>{status.text}</StatusChip>
      </header>

      <dl className="answer-card__stats">
        <div>
          <dt>Time</dt>
          <dd>{formatSeconds(answer.latency_ms)}</dd>
        </div>
        <div>
          <dt>Tokens in</dt>
          <dd>{formatTokens(answer.input_tokens)}</dd>
        </div>
        <div>
          <dt>Tokens out</dt>
          <dd>{formatTokens(answer.output_tokens)}</dd>
        </div>
      </dl>

      {answer.status === "error" && <Notice tone="danger">{answer.error}</Notice>}
      {answer.status === "pending" && <p className="hint">Waiting for an answer…</p>}
      {answer.status === "done" && (
        <>
          <div className="answer-card__text">{answer.text}</div>
          <div className="actions">
            <button type="button" className="btn--quiet" onClick={copy}>
              {copied ? "Copied" : "Copy answer"}
            </button>
          </div>
        </>
      )}
    </article>
  );
}

export function AnswerGrid({
  result,
  providers,
}: {
  result: AskResult;
  providers: AskProviderInfo[];
}) {
  return (
    <section className="answer-grid" aria-label="Answers">
      {result.answers.map((answer) => (
        <AnswerCard
          key={`${answer.provider}/${answer.model}`}
          answer={answer}
          label={providers.find((p) => p.provider === answer.provider)?.label ?? answer.provider}
        />
      ))}
    </section>
  );
}
