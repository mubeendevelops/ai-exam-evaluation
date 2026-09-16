// src/features/content/ParagraphDetailPanel.tsx — one paragraph in full,
// the questions already generated from it, and the two actions a teacher
// can take: generate more, or retire it. Same "sticky right rail" shape as
// QuestionDetailPanel.
import { Link } from "react-router-dom";

import { useParagraph, useSupersedeParagraph } from "../../api/queries";
import { ErrorMessage } from "../../components/ErrorMessage";
import { Loading } from "../../components/Loading";
import { QuestionStatusBadge } from "../questions/QuestionStatusBadge";
import { ParagraphStatusBadge } from "./ParagraphStatusBadge";

interface ParagraphDetailPanelProps {
  paragraphId: string;
  onClose: () => void;
}

export function ParagraphDetailPanel({ paragraphId, onClose }: ParagraphDetailPanelProps) {
  const paragraph = useParagraph(paragraphId);
  const supersede = useSupersedeParagraph();

  return (
    <div className="sticky top-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">Content detail</h2>
        <button type="button" onClick={onClose} className="text-xs text-slate-400 hover:text-slate-700">
          Close
        </button>
      </div>

      {paragraph.isLoading && <Loading label="Loading content…" />}
      {paragraph.error && (
        <div className="mt-3">
          <ErrorMessage message={(paragraph.error as Error).message} onRetry={() => void paragraph.refetch()} />
        </div>
      )}

      {paragraph.data && (
        <div className="mt-3 space-y-4">
          <div className="flex items-center gap-2">
            <ParagraphStatusBadge status={paragraph.data.status} />
            <span className="text-xs text-slate-400">{paragraph.data.source_document}</span>
          </div>

          <p className="whitespace-pre-wrap text-sm text-slate-900">{paragraph.data.content}</p>

          <details className="rounded-md border border-slate-100 bg-slate-50 p-2 text-xs text-slate-600">
            <summary className="cursor-pointer font-medium text-slate-700">
              Sentences ({paragraph.data.sentences.length})
            </summary>
            <ol className="mt-2 list-decimal space-y-1 pl-4">
              {paragraph.data.sentences.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </details>

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Questions generated ({paragraph.data.questions.length})
            </p>
            {paragraph.data.questions.length === 0 ? (
              <p className="mt-1 text-sm text-slate-500">None yet.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {paragraph.data.questions.map((q) => (
                  <li key={q.question_id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="line-clamp-1 text-slate-700">{q.content}</span>
                    <QuestionStatusBadge status={q.status} />
                  </li>
                ))}
              </ul>
            )}
          </div>

          {supersede.error && <p className="text-sm text-red-600">{(supersede.error as Error).message}</p>}

          <div className="flex gap-2">
            {paragraph.data.status === "active" && (
              <Link to={`/questions?paragraph_id=${paragraph.data.paragraph_id}`} className="btn-primary">
                Generate questions
              </Link>
            )}
            {paragraph.data.status === "active" && (
              <button
                type="button"
                className="rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-red-300"
                disabled={supersede.isPending}
                onClick={() => supersede.mutate(paragraph.data.paragraph_id)}
              >
                {supersede.isPending ? "Superseding…" : "Supersede"}
              </button>
            )}
            {paragraph.data.status === "superseded" && (
              <p className="text-sm text-slate-500">
                Superseded — no longer offered for new generation. Questions already generated from it are
                unaffected.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
