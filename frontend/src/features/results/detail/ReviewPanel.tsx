// src/features/results/detail/ReviewPanel.tsx — the score, the override
// form, and both append-only logs (ledger + reviews).
//
// AN OVERRIDE ADDS A REVIEW, IT DOES NOT EDIT THE AI'S SCORE
// (api/routers/evaluation.py, OverrideResponse.evaluation_results_untouched).
// This panel never lets a teacher "correct" `evaluation.score` in place — the
// form always posts a NEW answer_reviews row, and the AI's own score stays
// visible beside whatever final_marks reconciles to.
import { useState } from "react";

import { useOverride, type OverrideRequest, type ResultResponse } from "../../../api/queries";
import { ScoreValue } from "../StatusBadge";

type CappedListOf<T> = { items: T[]; total: number; truncated: boolean };

interface ReviewPanelProps {
  answerId: string;
  status: string;
  marksMax: number | null | undefined;
  evaluation: ResultResponse["evaluation"];
  finalMarks: ResultResponse["final_marks"];
  history: CappedListOf<NonNullable<ResultResponse["history"]>["items"][number]>;
  reviews: CappedListOf<NonNullable<ResultResponse["reviews"]>["items"][number]>;
}

/** RE-8: a finalized answer has no reopen path yet. Rendering an override
 * form anyway would promise an action the backend refuses (or worse, silently
 * accepts into a new review nobody can act on) — see docs/decisions/
 * reopening-a-finalized-answer.md. */
const FINALIZED_STATUS = "finalized";

export function ReviewPanel({ answerId, status, marksMax, evaluation, finalMarks, history, reviews }: ReviewPanelProps) {
  const override = useOverride(answerId);
  const [action, setAction] = useState<OverrideRequest["action"]>("overridden");
  const [marks, setMarks] = useState("");
  const [comment, setComment] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const isFinalized = status === FINALIZED_STATUS;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (action === "overridden" && marks.trim() === "") {
      setFormError("Enter the mark that should stand.");
      return;
    }
    override.mutate(
      {
        action,
        final_marks: action === "overridden" ? Number(marks) : null,
        comment: comment.trim() || null,
      },
      {
        onSuccess: () => {
          setMarks("");
          setComment("");
        },
        onError: (err) => setFormError((err as Error).message),
      },
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-400">AI score</p>
          <ScoreValue score={evaluation?.score} marksMax={marksMax} />
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-400">Final marks</p>
          {finalMarks.marks === null || finalMarks.marks === undefined ? (
            <span className="italic text-slate-400">Not decided</span>
          ) : (
            <span className="font-semibold text-slate-900">
              {finalMarks.marks}
              {marksMax != null && <span className="text-slate-400"> / {marksMax}</span>}
              <span className="ml-2 text-xs font-normal text-slate-500">
                ({finalMarks.source === "sme_override" ? "teacher override" : "AI"})
              </span>
            </span>
          )}
        </div>
      </div>

      <p className="text-xs text-slate-400">
        This is assistive scoring, never final on its own — a teacher's override always wins over the AI's number,
        and the AI's own score stays on record either way.
      </p>

      {isFinalized ? (
        <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
          This answer is finalized. There is no way to reopen a finalized answer yet.
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-3 border-t border-slate-100 pt-3">
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700">Action</span>
              <select
                className="select"
                value={action}
                onChange={(e) => setAction(e.target.value as OverrideRequest["action"])}
              >
                <option value="overridden">Override the mark</option>
                <option value="confirmed">Confirm the AI's score</option>
                <option value="flagged">Flag for someone else</option>
              </select>
            </label>
            {action === "overridden" && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-slate-700">Final marks</span>
                <input
                  type="number"
                  min={0}
                  step="0.5"
                  className="select w-28"
                  value={marks}
                  onChange={(e) => setMarks(e.target.value)}
                />
              </label>
            )}
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">Comment (optional)</span>
            <textarea
              className="select"
              rows={2}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={4000}
            />
          </label>
          {formError && <p className="text-sm text-red-600">{formError}</p>}
          <button type="submit" className="btn-primary" disabled={override.isPending}>
            {override.isPending ? "Saving…" : "Submit"}
          </button>
        </form>
      )}

      <details className="border-t border-slate-100 pt-2">
        <summary className="cursor-pointer text-sm font-medium text-slate-600">
          Score history ({history.total}){history.truncated && " — showing most recent"}
        </summary>
        <ul className="mt-2 space-y-1 text-xs text-slate-600">
          {history.items.map((entry) => (
            <li key={entry.evaluation_id} className="flex justify-between gap-2 border-b border-slate-100 py-1 last:border-0">
              <span>
                {entry.score} · {entry.evaluator_type}
                {entry.evaluator_model ? ` (${entry.evaluator_model})` : ""}
                {entry.is_current && <span className="ml-1 font-medium text-slate-900">current</span>}
              </span>
              <span className="text-slate-400">{new Date(entry.evaluated_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      </details>

      <details className="border-t border-slate-100 pt-2">
        <summary className="cursor-pointer text-sm font-medium text-slate-600">
          Review log ({reviews.total}){reviews.truncated && " — showing most recent"}
        </summary>
        <ul className="mt-2 space-y-1 text-xs text-slate-600">
          {reviews.items.length === 0 && <li className="italic text-slate-400">No reviews yet.</li>}
          {reviews.items.map((entry) => (
            <li key={entry.review_id} className="border-b border-slate-100 py-1 last:border-0">
              <div className="flex justify-between gap-2">
                <span className="font-medium text-slate-800">
                  {entry.action}
                  {entry.final_marks != null ? ` → ${entry.final_marks}` : ""}
                </span>
                <span className="text-slate-400">{new Date(entry.reviewed_at).toLocaleString()}</span>
              </div>
              {entry.comment && <p className="mt-0.5 text-slate-500">{entry.comment}</p>}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
