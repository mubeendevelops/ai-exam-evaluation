// src/features/questions/GatePipeline.tsx — makes the two review gates
// visually unmissable, in teacher language rather than schema language.
//
//   generate -> draft --Gate 1: Quality (confirm)--> confirmed --Gate 2: Publication--> live
//                  \--Gate 1: Quality (reject)------> rejected
//
// review fires only from draft; promote only from confirmed. There is no
// combined confirm-and-publish step (CLAUDE_CONTEXT.md §11) — this component
// never renders a single button that would imply one.
//
// Reviewer + timestamp: Gate 1's decision is durable (question_reviews,
// exposed as QuestionDetail.prior_reviews) so it renders here from the GET
// response on every visit. Gate 2 writes NO question_reviews row — only
// question_status_history, which GET /questions/{id} does not expose — so
// the promoting reviewer is only ever known for the lifetime of the browser
// tab that just clicked Promote (`recentPromotion`, passed down from the
// mutation's own response). Reloading the page loses it; that gap is stated
// rather than hidden behind a fabricated timestamp.
import type { PriorReview, QuestionDetail } from "../../api/queries";

type QuestionStatus = QuestionDetail["status"];

function short(id: string): string {
  return id.slice(0, 8);
}

interface PillProps {
  label: string;
  state: "done" | "current" | "pending" | "dead";
}

function Pill({ label, state }: PillProps) {
  const styles: Record<PillProps["state"], string> = {
    done: "bg-slate-200 text-slate-700",
    current: "bg-slate-900 text-white",
    pending: "bg-slate-100 text-slate-400",
    dead: "bg-red-100 text-red-700",
  };
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${styles[state]}`}>{label}</span>;
}

function GateArrow({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center px-1 text-center">
      <span className="text-slate-300">&rarr;</span>
      <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{label}</span>
    </div>
  );
}

export interface RecentPromotion {
  reviewerId: string;
}

interface GatePipelineProps {
  status: QuestionStatus;
  priorReviews: PriorReview[];
  recentPromotion?: RecentPromotion | null;
}

export function GatePipeline({ status, priorReviews, recentPromotion }: GatePipelineProps) {
  const qualityDecision = priorReviews[0]; // review fires once per question; a re-review is a 409, so at most one exists.

  // 'superseded' means a newer version replaced this question after it went
  // live, so it did pass both gates in the past — its pipeline reads as
  // fully done, not as though it never started.
  const draftState: PillProps["state"] = status === "draft" ? "current" : "done";
  const confirmedState: PillProps["state"] =
    status === "confirmed"
      ? "current"
      : status === "live" || status === "superseded"
        ? "done"
        : status === "rejected"
          ? "dead"
          : "pending";
  const liveState: PillProps["state"] = status === "live" ? "current" : status === "superseded" ? "done" : "pending";
  const rejectedState: PillProps["state"] = status === "rejected" ? "current" : "dead";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-1">
        <Pill label="Draft" state={draftState} />
        <GateArrow label="Gate 1 · Quality" />
        <Pill label="Confirmed" state={confirmedState} />
        <GateArrow label="Gate 2 · Publication" />
        <Pill label="Live" state={liveState} />
      </div>

      {status === "rejected" && (
        <div className="mt-2 flex items-center gap-1 pl-0">
          <span className="text-xs text-slate-400">from Draft, Gate 1 rejects to</span>
          <Pill label="Rejected" state={rejectedState} />
        </div>
      )}

      <p className="mt-3 text-xs text-slate-500">
        <span className="font-medium text-slate-600">Gate 1</span> asks: is this question correct and well-formed?{" "}
        <span className="font-medium text-slate-600">Gate 2</span> asks: may students now be asked it? Confirming a
        question never publishes it — publication is a separate, deliberate click.
      </p>

      {(status === "confirmed" || status === "rejected" || status === "live" || status === "superseded") && qualityDecision && (
        <div className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-600">
          <span className="font-medium text-slate-700">
            Gate 1 {qualityDecision.action === "confirmed" ? "confirmed" : "rejected"}
          </span>{" "}
          by reviewer {short(qualityDecision.reviewer_id)} on {new Date(qualityDecision.reviewed_at).toLocaleString()}
          {qualityDecision.comment && <span className="italic text-slate-500"> — "{qualityDecision.comment}"</span>}
        </div>
      )}

      {status === "live" && (
        <div className="mt-1 text-xs text-slate-600">
          {recentPromotion ? (
            <>
              <span className="font-medium text-slate-700">Gate 2 published</span> by reviewer{" "}
              {short(recentPromotion.reviewerId)} · just now
            </>
          ) : (
            <span className="italic text-slate-400">
              Publish reviewer/time isn't shown here — the question API records it in question_status_history but
              doesn't return that on GET, only the quality-review log above.
            </span>
          )}
        </div>
      )}
    </div>
  );
}
