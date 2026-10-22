/** Quick questions: one prompt, several models, answers side by side.
 *
 * No repo, no sandbox. The backend runs the claude/codex CLIs on the
 * subscription logins they already hold, and calls OpenRouter/Ollama
 * directly. Starting an ask returns at once; this view then polls each ask
 * that still has a pending answer, once a second, until all are in.
 *
 * History is deliberately ephemeral: earlier questions are kept for this
 * page session only, and the backend forgets them on restart.
 */

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchAsk,
  fetchAskProviders,
  startAsk,
  type AskContender,
  type AskProviderInfo,
  type AskResult,
} from "../api";
import { AnswerGrid } from "../components/AnswerGrid";
import { AskContenderRow } from "../components/AskContenderRow";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { defaultModelFor } from "../lib/ask";

const POLL_MS = 1000;
const MAX_CONTENDERS = 8;

function isPending(result: AskResult): boolean {
  return result.answers.some((a) => a.status === "pending");
}

function markLost(result: AskResult): AskResult {
  return {
    ...result,
    answers: result.answers.map((a) =>
      a.status === "pending"
        ? { ...a, status: "error", error: "The backend restarted and lost this answer." }
        : a,
    ),
  };
}

/** The first couple of usable providers, so the form starts ready to ask. */
function initialContenders(providers: AskProviderInfo[]): AskContender[] {
  return providers
    .filter((p) => p.configured)
    .slice(0, 2)
    .map((p) => ({ provider: p.provider, model: defaultModelFor(p) }));
}

export function AskView({ modelsRevision = 0 }: { modelsRevision?: number }) {
  const [providers, setProviders] = useState<AskProviderInfo[]>([]);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [contenders, setContenders] = useState<AskContender[]>([]);
  const [history, setHistory] = useState<AskResult[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const list = await fetchAskProviders();

        if (cancelled) return;
        setProviders(list);
        setContenders((prev) => (prev.length > 0 ? prev : initialContenders(list)));
      } catch (err) {
        if (!cancelled) {
          setProvidersError(err instanceof ApiError ? err.message : "Could not load providers");
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [modelsRevision]);

  // One interval while anything is pending, restarted only when the set of
  // pending asks changes -- so a failed tick simply retries on the next.
  const pendingKey = history
    .filter(isPending)
    .map((r) => r.ask_id)
    .join(",");

  useEffect(() => {
    if (pendingKey === "") return;
    const ids = pendingKey.split(",");

    async function refresh(id: string) {
      try {
        const fresh = await fetchAsk(id);

        setHistory((prev) => prev.map((r) => (r.ask_id === fresh.ask_id ? fresh : r)));
      } catch (err) {
        // Anything but a 404 is transient: the next tick retries. A 404
        // means the backend restarted and forgot the ask, so stop.
        if (!(err instanceof ApiError && err.status === 404)) return;
        setHistory((prev) => prev.map((r) => (r.ask_id === id ? markLost(r) : r)));
      }
    }

    const timer = setInterval(() => {
      for (const id of ids) void refresh(id);
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [pendingKey]);

  const current = history.find((r) => r.ask_id === currentId) ?? null;
  const usable = providers.some((p) => p.configured);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);

    try {
      const trimmed = question.trim();
      const { ask_id } = await startAsk(trimmed, contenders);

      const pending: AskResult = {
        ask_id,
        question: trimmed,
        created_at: Date.now() / 1000,
        answers: contenders.map((c) => ({
          ...c,
          status: "pending",
          text: "",
          error: null,
          latency_ms: null,
          input_tokens: null,
          output_tokens: null,
        })),
      };

      setHistory((prev) => [pending, ...prev]);
      setCurrentId(ask_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not ask the question");
    } finally {
      setBusy(false);
    }
  }

  function updateContender(index: number, next: AskContender) {
    setContenders((prev) => prev.map((c, i) => (i === index ? next : c)));
  }

  function addContender() {
    const unused =
      providers.find((p) => p.configured && !contenders.some((c) => c.provider === p.provider)) ??
      providers.find((p) => p.configured);

    if (!unused) return;
    setContenders((prev) => [...prev, { provider: unused.provider, model: defaultModelFor(unused) }]);
  }

  return (
    <div className="ask-view">
      <section className="ask-form" aria-labelledby="ask-heading">
        <h2 id="ask-heading">Ask</h2>
        <p className="hint">
          Put one question to several models and compare the answers. No repo needed. Claude Code
          and Codex use your subscription logins; nothing is saved.
        </p>

        {providersError && <Notice tone="danger">{providersError}</Notice>}
        {!providersError && providers.length > 0 && !usable && (
          <Notice tone="warn">
            No provider is set up on this machine yet. Log in to <code>claude</code> or{" "}
            <code>codex</code> once, set <code>OPENROUTER_API_KEY</code>, or start Ollama.
          </Notice>
        )}

        <form className="controls" ref={formRef} onSubmit={handleSubmit}>
          <Field id="ask-question" label="Question" hint="Ctrl/⌘ + Enter to ask.">
            <textarea
              id="ask-question"
              rows={4}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  formRef.current?.requestSubmit();
                }
              }}
              required
            />
          </Field>

          {contenders.map((contender, index) => (
            <AskContenderRow
              key={index}
              contender={contender}
              index={index}
              providers={providers}
              canRemove={contenders.length > 1}
              onChange={(next) => updateContender(index, next)}
              onRemove={() => setContenders((prev) => prev.filter((_, i) => i !== index))}
            />
          ))}

          <div className="actions">
            <button
              type="button"
              onClick={addContender}
              disabled={!usable || contenders.length >= MAX_CONTENDERS}
            >
              Add model
            </button>
            <button
              type="submit"
              className="btn--primary"
              disabled={busy || question.trim() === "" || contenders.length === 0}
            >
              {busy ? "Asking…" : "Ask"}
            </button>
          </div>
          {error && <Notice tone="danger">{error}</Notice>}
        </form>
      </section>

      {current && (
        <section className="ask-result" aria-labelledby="ask-result-heading">
          <h2 id="ask-result-heading">Answers</h2>
          <blockquote className="ask-result__question">{current.question}</blockquote>
          <AnswerGrid result={current} providers={providers} />
        </section>
      )}

      {history.length > 1 && (
        <nav className="ask-history" aria-label="Earlier questions">
          <h2>Earlier questions</h2>
          <ul>
            {history.map((r) => (
              <li key={r.ask_id}>
                <button
                  type="button"
                  className="btn--quiet"
                  aria-current={r.ask_id === currentId ? "true" : undefined}
                  onClick={() => setCurrentId(r.ask_id)}
                >
                  {r.question.length > 80 ? `${r.question.slice(0, 80)}…` : r.question}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
