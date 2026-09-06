// src/features/papers/PaperPreview.tsx — renders exactly what
// POST /papers/generate returned: never lets an incomplete paper look
// finished, and never invents topic data the API doesn't provide.
//
// is_complete / filled_marks / pattern_total_marks / warnings are RE-5's
// response shape (CLAUDE_CONTEXT.md §11's "Hardening pass (2026-09-05)" item
// 4) — a client can check completeness or the size of the gap without
// parsing prose. An unfilled MANDATORY slot never reaches this component: it
// is a 409 with nothing persisted, handled by the form instead.
import type { PaperGenerateResponse, PaperSection as PaperSectionType } from "../../api/queries";

interface PaperPreviewProps {
  paper: PaperGenerateResponse;
}

export function PaperPreview({ paper }: PaperPreviewProps) {
  return (
    <div>
      <div className="no-print flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-800">{paper.name}</h2>
        <button type="button" className="btn-secondary" onClick={() => window.print()}>
          Print / Export PDF
        </button>
      </div>

      <div className="print-area mt-3 space-y-4">
        <CompletenessBanner paper={paper} />
        {paper.warnings && paper.warnings.length > 0 && <WarningsList warnings={paper.warnings} />}
        <TopicDistributionNotice />

        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="text-base font-semibold text-slate-900">{paper.name}</h3>
          <p className="text-xs text-slate-500">
            Pattern: {paper.pattern_name} · {paper.filled_marks} / {paper.pattern_total_marks} marks filled
          </p>

          <div className="mt-4 space-y-5">
            {paper.sections
              .slice()
              .sort((a, b) => a.section_order - b.section_order)
              .map((section) => (
                <Section key={section.section_label} section={section} />
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function CompletenessBanner({ paper }: { paper: PaperGenerateResponse }) {
  const pct = paper.pattern_total_marks > 0 ? Math.min(100, (paper.filled_marks / paper.pattern_total_marks) * 100) : 0;
  return (
    <div
      className={`rounded-lg border p-4 ${
        paper.is_complete ? "border-green-200 bg-green-50" : "border-amber-200 bg-amber-50"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-sm font-semibold ${paper.is_complete ? "text-green-800" : "text-amber-800"}`}>
          {paper.is_complete ? "Complete — every slot is filled" : "Incomplete — some slots have no matching question"}
        </span>
        <span className={`text-sm font-medium ${paper.is_complete ? "text-green-700" : "text-amber-700"}`}>
          {paper.filled_marks} / {paper.pattern_total_marks} marks
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-white/60">
        <div
          className={`h-full rounded-full ${paper.is_complete ? "bg-green-500" : "bg-amber-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {!paper.is_complete && (
        <p className="mt-2 text-xs text-amber-700">
          This paper was still saved as generated — a shorter paper than the pattern intends. See the gaps below
          before handing it out.
        </p>
      )}
    </div>
  );
}

function WarningsList({ warnings }: { warnings: NonNullable<PaperGenerateResponse["warnings"]> }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-white p-4">
      <p className="text-sm font-semibold text-amber-800">
        {warnings.length} unfilled slot{warnings.length === 1 ? "" : "s"}
      </p>
      <ul className="mt-2 space-y-1 text-sm text-slate-700">
        {warnings.map((w) => (
          <li key={`${w.section}-${w.slot_label}`} className="border-b border-slate-100 pb-1 last:border-0">
            <span className="font-medium text-slate-900">
              {w.slot_label} ({w.section}, {w.marks}M)
            </span>
            : {w.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** RE-8: matching is style+marks only today — nothing in this schema tags a
 * question with a topic, and no endpoint returns one (topic_links exists in
 * the DB but is not wired into any question response). A paper can be
 * structurally complete and still lean entirely on one topic; this panel
 * says so honestly instead of rendering a distribution computed from data
 * that doesn't exist. Reading the assigned question text in the preview
 * below is the best available check until topic-aware matching lands. */
function TopicDistributionNotice() {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs text-slate-600">
      <p className="font-semibold text-slate-700">Topic distribution: not available</p>
      <p className="mt-1">
        Matching a paper's slots to questions today looks only at style and marks (RE-8) — nothing here checks
        topic coverage, and no endpoint returns a topic per question, so this screen can't compute a real
        distribution without inventing one. A structurally complete paper can still be topically lopsided. Read
        through the assigned questions in the preview below to judge topic balance yourself.
      </p>
    </div>
  );
}

function Section({ section }: { section: PaperSectionType }) {
  const total = section.slots.reduce((sum, slot) => sum + slot.marks, 0);
  return (
    <div>
      <div className="flex items-baseline justify-between border-b border-slate-200 pb-1">
        <h4 className="text-sm font-semibold text-slate-900">
          {section.section_label}
          <span className="ml-2 text-xs font-normal text-slate-500">
            {section.is_mandatory ? "Mandatory — answer all" : `Optional — answer ${section.choose_count}`}
          </span>
        </h4>
        <span className="text-xs font-medium text-slate-600">{total} marks</span>
      </div>

      <div className="mt-2 space-y-2">
        {section.slots.map((slot) => (
          <div key={slot.slot_label} className="text-sm">
            {slot.is_parent ? (
              <p className="font-medium text-slate-700">
                {slot.slot_label} · {slot.marks}M · {slot.style}
              </p>
            ) : null}
            <div className={slot.is_parent ? "mt-1 space-y-1 pl-4" : ""}>
              {slot.leaves.map((leaf) => (
                <div key={leaf.slot_id} className="rounded-md border border-slate-100 px-3 py-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium text-slate-800">
                      {leaf.slot_label} · {leaf.marks}M · {leaf.style}
                    </span>
                    {leaf.assigned && leaf.question_marks != null && leaf.question_marks !== leaf.marks && (
                      <span className="text-xs text-amber-600">matched at {leaf.question_marks}M (tolerance)</span>
                    )}
                  </div>
                  {leaf.assigned ? (
                    <p className="mt-1 text-slate-700">{leaf.question_content}</p>
                  ) : (
                    <p className="mt-1 italic text-slate-400">Unfilled — no matching live question.</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
