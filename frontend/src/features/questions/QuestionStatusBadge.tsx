// src/features/questions/QuestionStatusBadge.tsx — the compact status chip
// for question-bank list rows. The full two-gate pipeline (with reviewer +
// timestamp) lives in GatePipeline.tsx, in the detail panel; this is just
// "where is this question right now" at a glance.
import type { QuestionDetail } from "../../api/queries";

type QuestionStatus = QuestionDetail["status"];

const STATUS_STYLES: Record<QuestionStatus, string> = {
  draft: "bg-slate-100 text-slate-600",
  confirmed: "bg-blue-100 text-blue-700",
  rejected: "bg-red-100 text-red-700",
  live: "bg-green-100 text-green-700",
  superseded: "bg-slate-100 text-slate-400",
};

const STATUS_LABELS: Record<QuestionStatus, string> = {
  draft: "Draft",
  confirmed: "Confirmed",
  rejected: "Rejected",
  live: "Live",
  superseded: "Superseded",
};

export function QuestionStatusBadge({ status }: { status: QuestionStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}
