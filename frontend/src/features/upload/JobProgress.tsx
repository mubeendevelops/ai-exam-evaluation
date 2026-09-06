// src/features/upload/JobProgress.tsx — renders one evaluation_jobs row as a
// stage list, honestly.
//
// api/schemas/jobs.py's JobProgress is explicit that `percent` is a STAGE
// MARKER, not a measured fraction (no progress callback exists yet inside
// core/booklet_evaluator.py — CLAUDE_CONTEXT.md §11). So this component never
// renders a smoothly-animating bar: it renders the fixed list of stages for
// this job's type, with the current one highlighted. That is honest whether
// or not RE-2's callback work has landed — it degrades to "which stage" and
// never fakes "how far into the stage".
import type { JobResponse } from "../../api/queries";

const INGEST_STAGES = ["checking", "fetching", "rasterizing", "segmenting", "persisting", "done"];
const EVAL_STAGES = ["loading", "evaluating", "persisting", "done"];

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  checking: "Checking upload",
  fetching: "Fetching booklet",
  rasterizing: "Rasterizing pages",
  segmenting: "Detecting regions",
  loading: "Loading regions",
  evaluating: "Scoring answers",
  persisting: "Saving results",
  done: "Done",
  failed: "Failed",
};

function stagesFor(jobType: string): string[] {
  return jobType === "booklet_ingest" ? INGEST_STAGES : EVAL_STAGES;
}

interface JobProgressProps {
  job: JobResponse;
  label: string;
}

export function JobProgress({ job, label }: JobProgressProps) {
  const stages = stagesFor(job.job_type);
  const currentStage = job.status === "failed" ? "failed" : job.progress.stage;
  const currentIndex = stages.indexOf(currentStage);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">{label}</h3>
        <span className="text-xs uppercase tracking-wide text-slate-400">{job.status}</span>
      </div>

      <ol className="mt-4 flex flex-wrap gap-2" aria-label={`${label} stages`}>
        {stages.map((stage, index) => {
          const isCurrent = stage === currentStage;
          const isPast = currentIndex >= 0 && index < currentIndex;
          return (
            <li
              key={stage}
              aria-current={isCurrent ? "step" : undefined}
              className={[
                "rounded-full px-3 py-1 text-xs font-medium",
                isCurrent ? "bg-slate-900 text-white" : "",
                !isCurrent && isPast ? "bg-slate-200 text-slate-600" : "",
                !isCurrent && !isPast ? "bg-slate-100 text-slate-400" : "",
              ].join(" ")}
            >
              {STAGE_LABELS[stage] ?? stage}
            </li>
          );
        })}
      </ol>

      <p className="mt-3 text-sm text-slate-600">{job.progress.message}</p>

      {Object.keys(job.progress.counts).length > 0 && (
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
          {Object.entries(job.progress.counts).map(([key, value]) => (
            <div key={key} className="flex gap-1">
              <dt className="capitalize">{key}:</dt>
              <dd className="font-medium text-slate-700">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {job.progress.attempts > 1 && (
        <p className="mt-2 text-xs text-amber-600">
          Retried {job.progress.attempts - 1} time{job.progress.attempts > 2 ? "s" : ""} after an interrupted run.
        </p>
      )}

      {job.status === "failed" && job.error && <FailureMessage error={job.error} />}
    </div>
  );
}

/** Turns a couple of known backend error shapes into plain language,
 * without hiding the original error text. */
function FailureMessage({ error }: { error: string }) {
  const isMissingIngestion = /NoRegionsError|has to be INGESTED/i.test(error);

  return (
    <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      {isMissingIngestion ? (
        <p className="font-medium">
          This booklet hasn't been segmented into answer regions yet, so there is nothing to score. Ingestion should
          normally happen automatically — try evaluating again, and contact your admin if this keeps happening.
        </p>
      ) : (
        <p className="font-medium">This job failed.</p>
      )}
      <p className="mt-1 whitespace-pre-wrap text-xs text-red-700">{error}</p>
    </div>
  );
}
