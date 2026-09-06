// src/features/questions/QuestionDetailPanel.tsx — one question in full,
// plus the two gates that decide whether it's ever shown to a student.
//
// review fires only from draft, promote only from confirmed. This panel
// renders exactly one action set for whichever status the question is
// actually in — never both, and never a single button that does both (there
// is deliberately no combined confirm-and-publish endpoint, and a backend
// test sweeps for one being added; this UI must not build a client-side
// shortcut that just chains the two calls back to back).
import { useState } from "react";

import { useQuestion, useReviewQuestion, usePromoteQuestion, type QuestionDetail } from "../../api/queries";
import { ErrorMessage } from "../../components/ErrorMessage";
import { Loading } from "../../components/Loading";
import { useFormDraft } from "../../hooks/useFormDraft";
import { GatePipeline, type RecentPromotion } from "./GatePipeline";
import { QuestionStatusBadge } from "./QuestionStatusBadge";

interface QuestionDetailPanelProps {
  questionId: string;
  onClose: () => void;
}

export function QuestionDetailPanel({ questionId, onClose }: QuestionDetailPanelProps) {
  const question = useQuestion(questionId);
  const [recentPromotion, setRecentPromotion] = useState<RecentPromotion | null>(null);

  return (
    <div className="sticky top-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">Question detail</h2>
        <button type="button" onClick={onClose} className="text-xs text-slate-400 hover:text-slate-700">
          Close
        </button>
      </div>

      {question.isLoading && <Loading label="Loading question…" />}
      {question.error && (
        <div className="mt-3">
          <ErrorMessage message={(question.error as Error).message} onRetry={() => void question.refetch()} />
        </div>
      )}
      {question.data && (
        <Body
          question={question.data}
          recentPromotion={recentPromotion}
          onPromoted={(reviewerId) => setRecentPromotion({ reviewerId })}
        />
      )}
    </div>
  );
}

function Body({
  question,
  recentPromotion,
  onPromoted,
}: {
  question: QuestionDetail;
  recentPromotion: RecentPromotion | null;
  onPromoted: (reviewerId: string) => void;
}) {
  return (
    <div className="mt-3 space-y-4">
      <div className="flex items-center gap-2">
        <QuestionStatusBadge status={question.status} />
        {question.is_ai_generated && (
          <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700">
            AI-generated
          </span>
        )}
        {question.style && <span className="text-xs text-slate-400">{question.style}</span>}
        {question.marks_max != null && <span className="text-xs text-slate-400">{question.marks_max} marks</span>}
      </div>

      <p className="whitespace-pre-wrap text-sm text-slate-900">{question.content}</p>

      {question.source_paragraph_content && (
        <details className="rounded-md border border-slate-100 bg-slate-50 p-2 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-700">Source paragraph</summary>
          <p className="mt-2 whitespace-pre-wrap">{question.source_paragraph_content}</p>
        </details>
      )}

      <GatePipeline status={question.status} priorReviews={question.prior_reviews ?? []} recentPromotion={recentPromotion} />

      {question.status === "draft" && <QualityGateForm questionId={question.question_id} />}
      {question.status === "confirmed" && (
        <PublicationGateAction questionId={question.question_id} onPromoted={onPromoted} />
      )}
      {question.status === "rejected" && (
        <p className="text-sm text-slate-500">
          Rejected at Gate 1 — this question stops here. There is no reopen path in this build.
        </p>
      )}
      {question.status === "superseded" && (
        <p className="text-sm text-slate-500">Superseded by a newer version of this question.</p>
      )}
    </div>
  );
}

function QualityGateForm({ questionId }: { questionId: string }) {
  const review = useReviewQuestion(questionId);
  // Same rationale as ReviewPanel's override draft: a forced logout mid-typed
  // comment (expired refresh token, hard redirect to /login) must not
  // silently discard it — see useFormDraft's docstring.
  const { draft, save: saveDraft, clear: clearDraft } = useFormDraft<{ comment: string }>(
    `ai-eval.review-draft:${questionId}`,
  );
  const [comment, setComment] = useState(draft?.comment ?? "");

  function updateComment(next: string) {
    setComment(next);
    saveDraft({ comment: next });
  }

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Gate 1 — is this question correct?
      </p>
      {draft && (
        <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Restored a comment you hadn't submitted yet — your session may have expired before it saved.
        </p>
      )}
      <textarea
        className="select mt-2 w-full"
        rows={2}
        placeholder="Comment (optional)"
        value={comment}
        onChange={(e) => updateComment(e.target.value)}
        maxLength={4000}
      />
      {review.error && <p className="mt-2 text-sm text-red-600">{(review.error as Error).message}</p>}
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={review.isPending}
          onClick={() =>
            review.mutate(
              { action: "confirm", comment: comment.trim() || null },
              { onSuccess: clearDraft },
            )
          }
        >
          {review.isPending ? "Saving…" : "Confirm — content is correct"}
        </button>
        <button
          type="button"
          className="rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-red-300"
          disabled={review.isPending}
          onClick={() =>
            review.mutate(
              { action: "reject", comment: comment.trim() || null },
              { onSuccess: clearDraft },
            )
          }
        >
          Reject
        </button>
      </div>
    </div>
  );
}

function PublicationGateAction({
  questionId,
  onPromoted,
}: {
  questionId: string;
  onPromoted: (reviewerId: string) => void;
}) {
  const promote = usePromoteQuestion(questionId);

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Gate 2 — may students be asked this now?
      </p>
      <p className="mt-1 text-xs text-slate-500">
        This question already passed Gate 1. Publishing is a separate, deliberate decision — students can be asked a
        live question in an exam the moment this succeeds.
      </p>
      {promote.error && <p className="mt-2 text-sm text-red-600">{(promote.error as Error).message}</p>}
      <button
        type="button"
        className="btn-primary mt-2"
        disabled={promote.isPending}
        onClick={() =>
          promote.mutate(undefined, {
            onSuccess: (data) => onPromoted(data.reviewer_id),
          })
        }
      >
        {promote.isPending ? "Publishing…" : "Publish to live"}
      </button>
    </div>
  );
}
