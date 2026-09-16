// src/features/content/ParagraphStatusBadge.tsx — the compact status chip
// for picker rows. Same idiom as QuestionStatusBadge, over the smaller
// paragraph_status vocabulary (migration 002).
import type { ParagraphSummary } from "../../api/queries";

type ParagraphStatus = ParagraphSummary["status"];

const STATUS_STYLES: Record<ParagraphStatus, string> = {
  active: "bg-green-100 text-green-700",
  superseded: "bg-slate-100 text-slate-400",
};

const STATUS_LABELS: Record<ParagraphStatus, string> = {
  active: "Active",
  superseded: "Superseded",
};

export function ParagraphStatusBadge({ status }: { status: ParagraphStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}
