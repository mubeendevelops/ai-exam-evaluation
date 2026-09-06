// src/features/results/StatusBadge.tsx — shared status/needs-review chips
// used by both the results list and the detail screen.
import type { ResultSummary } from "../../api/queries";

const STATUS_STYLES: Record<ResultSummary["status"], string> = {
  pending_evaluation: "bg-slate-100 text-slate-600",
  ai_scored: "bg-blue-100 text-blue-700",
  sme_reviewed: "bg-purple-100 text-purple-700",
  finalized: "bg-green-100 text-green-700",
  flagged: "bg-amber-100 text-amber-800",
};

const STATUS_LABELS: Record<ResultSummary["status"], string> = {
  pending_evaluation: "Pending evaluation",
  ai_scored: "AI scored",
  sme_reviewed: "Teacher reviewed",
  finalized: "Finalized",
  flagged: "Flagged",
};

export function StatusBadge({ status }: { status: ResultSummary["status"] }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}

export function NeedsReviewBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-700">
      <span aria-hidden="true">●</span> Needs your review
    </span>
  );
}

/** A null score is NOT zero (CLAUDE_CONTEXT.md §7D) — always route it through
 * this rather than letting `?? 0` slip in anywhere near a score. */
export function ScoreValue({ score, marksMax }: { score: number | null | undefined; marksMax?: number | null }) {
  if (score === null || score === undefined) {
    return <span className="italic text-slate-400">Not evaluated</span>;
  }
  return (
    <span className="font-medium text-slate-900">
      {score}
      {marksMax != null && <span className="text-slate-400"> / {marksMax}</span>}
    </span>
  );
}
