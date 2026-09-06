// src/routes/Upload.tsx — drag-drop a booklet, bind it to an exam/student/
// paper, queue evaluation, and watch it through to a scored result.
//
// UPLOAD AND EVALUATE ARE TWO CALLS, deliberately not collapsed (§11): an
// upload is just a stored PDF, and POST /evaluate is what needs the
// exam/student/paper context and actually queues work. This screen still
// does both back to back for the common case (someone uploading a booklet
// they intend to evaluate right now), but keeps them as two distinct
// mutations so a future "evaluate an already-uploaded booklet again" flow
// has nothing to restructure.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  JOB_STUCK_TIMEOUT_MS,
  useEvaluate,
  useJobPolling,
  useUploadBooklet,
  type UploadResponse,
} from "../api/queries";
import { Loading } from "../components/Loading";
import { BookletForm, type BookletSelection } from "../features/upload/BookletForm";
import { Dropzone } from "../features/upload/Dropzone";
import { JobProgress } from "../features/upload/JobProgress";

type Phase = "picking" | "uploading" | "queuing" | "ingesting" | "evaluating" | "done";

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [selection, setSelection] = useState<Partial<BookletSelection>>({});
  const [phase, setPhase] = useState<Phase>("picking");
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [pollingJobId, setPollingJobId] = useState<string | undefined>();
  const [evalJobId, setEvalJobId] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);

  const uploadMutation = useUploadBooklet();
  const evaluateMutation = useEvaluate();
  const jobQuery = useJobPolling(pollingJobId);

  const isComplete = Boolean(selection.examId && selection.studentId && selection.paperId);
  const busy = phase !== "picking" && phase !== "done";

  function reset() {
    setFile(null);
    setSelection({});
    setPhase("picking");
    setUpload(null);
    setPollingJobId(undefined);
    setEvalJobId(undefined);
    setFormError(null);
  }

  async function start() {
    if (!file || !isComplete) return;
    setFormError(null);

    setPhase("uploading");
    let uploaded: UploadResponse;
    try {
      uploaded = await uploadMutation.mutateAsync(file);
    } catch (err) {
      setFormError((err as Error).message);
      setPhase("picking");
      return;
    }
    setUpload(uploaded);

    setPhase("queuing");
    try {
      const evaluation = await evaluateMutation.mutateAsync({
        upload_id: uploaded.upload_id,
        exam_id: selection.examId!,
        student_id: selection.studentId!,
        paper_id: selection.paperId!,
      });
      if (evaluation.job_type === "booklet_ingest") {
        setPhase("ingesting");
        setPollingJobId(evaluation.job_id);
        setEvalJobId(evaluation.ingest_job_id === evaluation.job_id ? undefined : (evaluation.ingest_job_id ?? undefined));
      } else {
        setPhase("evaluating");
        setEvalJobId(evaluation.job_id);
        setPollingJobId(evaluation.job_id);
      }
    } catch (err) {
      setFormError((err as Error).message);
      setPhase("picking");
    }
  }

  // Once the ingest job finishes, chain to the evaluation job it queued.
  useEffect(() => {
    if (phase !== "ingesting" || jobQuery.data?.status !== "succeeded") return;
    const nextJobId = (jobQuery.data.result?.evaluation_job_id as string | undefined) ?? evalJobId;
    if (!nextJobId) return;
    setPhase("evaluating");
    setEvalJobId(nextJobId);
    setPollingJobId(nextJobId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, jobQuery.data]);

  useEffect(() => {
    if (phase === "evaluating" && jobQuery.data?.status === "succeeded") {
      setPhase("done");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, jobQuery.data]);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-lg font-semibold text-slate-900">Upload answer sheets</h1>
      <p className="mt-1 text-sm text-slate-500">
        Upload a scanned answer booklet, bind it to an exam and student, and queue it for AI evaluation.
      </p>

      <div className="mt-6 space-y-6">
        {!file ? (
          <Dropzone onFileSelected={setFile} disabled={busy} />
        ) : (
          <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3">
            <div>
              <p className="text-sm font-medium text-slate-900">{file.name}</p>
              <p className="text-xs text-slate-500">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
            {phase === "picking" && (
              <button type="button" className="text-sm text-slate-500 hover:text-slate-800" onClick={() => setFile(null)}>
                Remove
              </button>
            )}
          </div>
        )}

        {file && (
          <>
            <BookletForm value={selection} onChange={setSelection} disabled={busy} />

            {formError && (
              <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                {formError}
              </div>
            )}

            {phase === "picking" && (
              <button type="button" className="btn-primary" disabled={!isComplete || uploadMutation.isPending} onClick={() => void start()}>
                Upload and evaluate
              </button>
            )}

            {phase === "uploading" && <Loading label="Uploading booklet…" />}
            {phase === "queuing" && <Loading label="Queuing evaluation…" />}

            {(phase === "ingesting" || phase === "evaluating") && (
              <div className="space-y-4">
                {jobQuery.isLoading && <Loading label="Checking job status…" />}
                {jobQuery.error && (
                  <p className="text-sm text-red-600">{(jobQuery.error as Error).message}</p>
                )}
                {jobQuery.data && (
                  <JobProgress
                    job={jobQuery.data}
                    label={phase === "ingesting" ? "Segmenting the booklet" : "Scoring the booklet"}
                  />
                )}
                {jobQuery.stuck && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    <p className="font-medium">
                      This job has been running for over {Math.round(JOB_STUCK_TIMEOUT_MS / 60000)} minutes with no
                      automatic recovery yet. It may still finish — check again, or come back later.
                    </p>
                    <button type="button" className="btn-secondary mt-2" onClick={jobQuery.checkAgain}>
                      Check status again
                    </button>
                  </div>
                )}
                {jobQuery.data?.status === "failed" && (
                  <button type="button" className="btn-secondary" onClick={reset}>
                    Start over
                  </button>
                )}
              </div>
            )}

            {phase === "done" && upload && (
              <div className="space-y-4 rounded-lg border border-green-200 bg-green-50 p-5">
                <p className="text-sm font-medium text-green-800">Evaluation finished.</p>
                <p className="text-sm text-green-700">
                  Review the AI's scores, per-question breakdowns, and override anything that needs a teacher's
                  judgment.
                </p>
                <div className="flex gap-3">
                  <Link
                    to={`/results?exam_id=${selection.examId}&student_id=${selection.studentId}`}
                    className="btn-primary"
                  >
                    View results
                  </Link>
                  <button type="button" className="btn-secondary" onClick={reset}>
                    Upload another booklet
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
