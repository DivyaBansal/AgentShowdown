/** Talk to an agent: answer the question it stopped to ask, or send a
 *  follow-up instruction once it's idle.
 *
 * This is pause-then-resume, not live chat -- headless agent CLIs have no
 * REPL to type into, so a reply is relayed as the next turn's prompt. The
 * server accepts it at once and resumes the agent in the background; the
 * job's new status then arrives over the live stream like any other.
 *
 * Renders nothing for a job that can't take a reply right now (it's
 * running, or stopped and needs a respawn first).
 */

import { useState, type FormEvent } from "react";
import { ApiError, answerJob, answerJobWithOption, followupJob, type Job } from "../api";
import { canFollowUp, questionOptions } from "../lib/jobs";
import { Notice } from "./ui/Notice";

export function ReplyForm({ job, showQuestion = true }: { job: Job; showQuestion?: boolean }) {
  // Keyed on status so "sent" resets once the job moves on: the same job
  // can stop to ask a second question later, and that one needs a fresh
  // form rather than the previous "sent" notice.
  return <ReplyFormBody key={job.status} job={job} showQuestion={showQuestion} />;
}

function ReplyFormBody({ job, showQuestion }: { job: Job; showQuestion: boolean }) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const answering = job.status === "awaiting_input";

  if (!answering && !canFollowUp(job.status)) return null;

  const options = answering ? questionOptions(job) : [];
  const fieldId = `reply-${job.sandbox_name}`;

  async function send(reply: () => Promise<{ status: string }>) {
    setError(null);
    setPending(true);

    try {
      await reply();
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send the reply");
    } finally {
      setPending(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();

    if (trimmed === "") return;

    void send(() =>
      answering
        ? answerJob(job.sandbox_name, trimmed)
        : followupJob(job.sandbox_name, trimmed),
    );
  }

  if (sent) {
    return (
      <Notice tone="ok">
        {answering ? "Answer sent. The agent is resuming." : "Follow-up sent. The agent is resuming."}
      </Notice>
    );
  }

  return (
    <form className="stack stack--tight" onSubmit={handleSubmit}>
      {answering && showQuestion && job.detail && <p className="reply__question">{job.detail}</p>}
      {options.length > 0 && (
        <div className="actions" role="group" aria-label="Suggested answers">
          {options.map((option, index) => (
            <button
              key={option}
              type="button"
              disabled={pending}
              onClick={() => void send(() => answerJobWithOption(job.sandbox_name, index))}
            >
              {option}
            </button>
          ))}
        </div>
      )}
      <label htmlFor={fieldId}>{answering ? "Your answer" : "Follow-up instruction"}</label>
      <textarea
        id={fieldId}
        rows={answering ? 2 : 3}
        value={text}
        maxLength={4000}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="actions">
        <button type="submit" disabled={pending || text.trim() === ""}>
          {answering ? "Send answer" : "Send follow-up"}
        </button>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
    </form>
  );
}
