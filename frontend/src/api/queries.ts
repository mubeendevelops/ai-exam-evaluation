// src/api/queries.ts — shared TanStack Query hooks for the Upload and
// Results screens. One place for query keys and query functions so both
// screens (and the polling loop) agree on how a job/result is cached.
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "./client";
import { describeApiError } from "./errors";
import type { components } from "./schema";

export type ExamSummary = components["schemas"]["ExamSummary"];
export type StudentSummary = components["schemas"]["StudentSummary"];
export type PaperSummary = components["schemas"]["PaperSummary"];
export type UploadResponse = components["schemas"]["UploadResponse"];
export type UploadSummary = components["schemas"]["UploadSummary"];
export type JobResponse = components["schemas"]["JobResponse"];
export type EvaluateResponse = components["schemas"]["EvaluateResponse"];
export type ResultSummary = components["schemas"]["ResultSummary"];
export type ResultResponse = components["schemas"]["ResultResponse"];
export type RegionDetail = components["schemas"]["RegionDetail"];
export type PageSummary = components["schemas"]["PageSummary"];
export type SignalDetail = components["schemas"]["SignalDetail"];
export type OverrideRequest = components["schemas"]["OverrideRequest"];
export type OverrideResponse = components["schemas"]["OverrideResponse"];
export type QuestionSummary = components["schemas"]["QuestionSummary"];
export type QuestionDetail = components["schemas"]["QuestionDetail"];
export type QuestionListResponse = components["schemas"]["QuestionListResponse"];
export type PriorReview = components["schemas"]["PriorReview"];
export type GenerateQuestionsRequest = components["schemas"]["GenerateQuestionsRequest"];
export type GenerateQuestionsResponse = components["schemas"]["GenerateQuestionsResponse"];
export type GeneratedQuestion = components["schemas"]["GeneratedQuestion"];
export type ReviewRequest = components["schemas"]["ReviewRequest"];
export type TransitionResponse = components["schemas"]["TransitionResponse"];
export type PaperGenerateRequest = components["schemas"]["PaperGenerateRequest"];
export type PaperGenerateResponse = components["schemas"]["PaperGenerateResponse"];
export type PaperSection = components["schemas"]["PaperSection"];
export type PaperSlot = components["schemas"]["PaperSlot"];
export type AssignedSlot = components["schemas"]["AssignedSlot"];
export type SlotWarning = components["schemas"]["SlotWarning"];

async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await promise;
  if (error) {
    throw new Error(describeApiError(error, response).message);
  }
  return data as T;
}

// ─────────────────────────── pickers (Upload screen) ───────────────────────

export function useExams() {
  return useQuery({
    queryKey: ["exams"],
    queryFn: () => unwrap(api.GET("/api/v1/exams", { params: { query: { limit: 200 } } })),
  });
}

export function useStudents(examId: string | undefined) {
  return useQuery({
    queryKey: ["students", examId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/students", { params: { query: { exam_id: examId, limit: 200 } } })),
    enabled: Boolean(examId),
  });
}

export interface PapersFilter {
  status?: PaperSummary["status"];
  pattern_id?: string;
}

export function usePapers(filter: PapersFilter = {}) {
  return useQuery({
    queryKey: ["papers", filter],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/papers", {
          params: { query: { status: filter.status ?? null, pattern_id: filter.pattern_id ?? null, limit: 200 } },
        }),
      ),
  });
}

export function useUploads(bound?: boolean) {
  return useQuery({
    queryKey: ["uploads", bound ?? null],
    queryFn: () =>
      unwrap(api.GET("/api/v1/uploads", { params: { query: { bound: bound ?? null, limit: 50 } } })),
  });
}

// ─────────────────────────────── upload + evaluate ──────────────────────────

export function useUploadBooklet() {
  return useMutation({
    mutationFn: (file: File) =>
      unwrap(
        api.POST("/api/v1/upload", {
          // openapi-fetch serializes an object body to FormData when the
          // operation's requestBody is multipart/form-data.
          body: { file: file as unknown as string },
        }),
      ),
  });
}

export interface EvaluateParams {
  upload_id: string;
  exam_id: string;
  student_id: string;
  paper_id: string;
}

export function useEvaluate() {
  return useMutation({
    mutationFn: (body: EvaluateParams) =>
      unwrap(api.POST("/api/v1/evaluate", { body: { ...body, stub: false, stub_llm: false } })),
  });
}

/** Statuses that mean "stop polling". */
const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed"]);

/** No reaper exists yet for a worker that dies mid-job (CLAUDE_CONTEXT.md
 * §11) — a job can sit `running` forever. This is the client-side backstop:
 * past this many milliseconds since the job was created, stop polling and
 * show a plain "this is taking too long" state instead of an infinite
 * spinner. */
export const JOB_STUCK_TIMEOUT_MS = 5 * 60 * 1000;

/** Polls GET /jobs/{id} with backoff (2s -> 4s -> 8s -> capped at 15s) while
 * the job is queued/running, and stops on a terminal status or the stuck
 * timeout. */
export function useJobPolling(jobId: string | undefined) {
  const [attempt, setAttempt] = useState(0);
  const startedAtRef = useRef<number>(Date.now());
  const queryClient = useQueryClient();

  useEffect(() => {
    setAttempt(0);
    startedAtRef.current = Date.now();
  }, [jobId]);

  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => unwrap(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId! } } })),
    enabled: Boolean(jobId),
    refetchInterval: (q) => {
      const job = q.state.data as JobResponse | undefined;
      if (!job || TERMINAL_JOB_STATUSES.has(job.status)) return false;
      if (Date.now() - startedAtRef.current > JOB_STUCK_TIMEOUT_MS) return false;
      const delay = Math.min(2000 * 2 ** attempt, 15000);
      return delay;
    },
    refetchIntervalInBackground: true,
  });

  useEffect(() => {
    if (query.isFetching) setAttempt((a) => a + 1);
  }, [query.isFetching]);

  const stuck =
    Boolean(jobId) &&
    !!query.data &&
    !TERMINAL_JOB_STATUSES.has(query.data.status) &&
    Date.now() - startedAtRef.current > JOB_STUCK_TIMEOUT_MS;

  /** Manual "check again" after the stuck timeout: refetches once and gives
   * the job another full timeout window rather than resuming automatic
   * polling forever — there is still no reaper on the server, so a truly
   * dead job stays reachable only by asking again. */
  function checkAgain() {
    startedAtRef.current = Date.now();
    setAttempt(0);
    void queryClient.invalidateQueries({ queryKey: ["job", jobId] });
  }

  return { ...query, stuck, checkAgain };
}

// ────────────────────────────────── results ─────────────────────────────────

export interface ResultsFilter {
  exam_id?: string;
  student_id?: string;
  status?: ResultSummary["status"];
  needs_review?: boolean;
}

export function useResultsList(filter: ResultsFilter) {
  return useQuery({
    queryKey: ["results", filter],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/results", {
          params: {
            query: {
              exam_id: filter.exam_id ?? null,
              student_id: filter.student_id ?? null,
              status: filter.status ?? null,
              needs_review: filter.needs_review ?? null,
              limit: 200,
            },
          },
        }),
      ),
  });
}

export function useResult(answerId: string | undefined, options?: Partial<UseQueryOptions<ResultResponse>>) {
  return useQuery({
    queryKey: ["result", answerId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/results/{answer_id}", { params: { path: { answer_id: answerId! } } })),
    enabled: Boolean(answerId),
    ...options,
  });
}

export function usePageImage(answerId: string | undefined, pageNumber: number | undefined) {
  return useQuery({
    queryKey: ["page-image", answerId, pageNumber],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/results/{answer_id}/pages/{page_number}/image", {
          params: { path: { answer_id: answerId!, page_number: pageNumber! } },
        }),
      ),
    enabled: Boolean(answerId) && pageNumber !== undefined,
    // The URL expires in 300s (PAGE_IMAGE_URL_TTL_SECONDS) — refetch well
    // before that so a reviewer lingering on one page doesn't hit a dead
    // <img> src.
    staleTime: 4 * 60 * 1000,
  });
}

export function useOverride(answerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OverrideRequest) =>
      unwrap(api.POST("/api/v1/results/{answer_id}/override", { params: { path: { answer_id: answerId } }, body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["result", answerId] });
      void queryClient.invalidateQueries({ queryKey: ["results"] });
    },
  });
}

// ─────────────────────────────── question bank ──────────────────────────────
// The bank (questions + papers, 12 tables) is SHARED across colleges by
// design — no college_id column, no RLS (CLAUDE_CONTEXT.md §11). These hooks
// authenticate via get_tenant_conn like everything else but never filter on
// a college; do not add a collegeId param here to make that look tenanted.

export interface QuestionsFilter {
  status?: QuestionSummary["status"];
  style?: NonNullable<QuestionSummary["style"]>;
  source_type?: NonNullable<QuestionSummary["source_type"]>;
  is_ai_generated?: boolean;
  paper_id?: string;
}

export function useQuestionsList(filter: QuestionsFilter, page: { limit: number; offset: number }) {
  return useQuery({
    queryKey: ["questions", filter, page],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/questions", {
          params: {
            query: {
              status: filter.status ?? null,
              style: filter.style ?? null,
              source_type: filter.source_type ?? null,
              is_ai_generated: filter.is_ai_generated ?? null,
              paper_id: filter.paper_id ?? null,
              limit: page.limit,
              offset: page.offset,
            },
          },
        }),
      ),
  });
}

export function useQuestion(questionId: string | undefined) {
  return useQuery({
    queryKey: ["question", questionId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/questions/{question_id}", { params: { path: { question_id: questionId! } } })),
    enabled: Boolean(questionId),
  });
}

export function useGenerateQuestions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: GenerateQuestionsRequest) => unwrap(api.POST("/api/v1/questions/generate", { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}

/** Gate 1 — quality. Fires only from 'draft'; a re-review is a 409, not an
 * overwrite (see the schema's ReviewRequest docstring) — this hook does not
 * retry or paper over that, it surfaces whatever describeApiError produces. */
export function useReviewQuestion(questionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewRequest) =>
      unwrap(api.POST("/api/v1/questions/{question_id}/review", { params: { path: { question_id: questionId } }, body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["question", questionId] });
      void queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}

/** Gate 2 — publication. Fires only from 'confirmed'; posting this against a
 * draft is a 409 and the row stays a draft (CLAUDE_CONTEXT.md §11). Takes no
 * body — the promoting reviewer is the authenticated caller (RE-4). There is
 * deliberately no combined confirm-and-publish call: do not chain this onto
 * useReviewQuestion's onSuccess anywhere. */
export function usePromoteQuestion(questionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/questions/{question_id}/promote", { params: { path: { question_id: questionId } } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["question", questionId] });
      void queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}

// ─────────────────────────────── paper generation ───────────────────────────

export function useGeneratePaper() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PaperGenerateRequest) => unwrap(api.POST("/api/v1/papers/generate", { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
  });
}
