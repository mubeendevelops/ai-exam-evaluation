import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { useToast } from '../ui'
import { reviewKey, type Review, type ReviewAnswer } from './queries'

interface Reply<T> {
  data?: T
  error?: unknown
  response: Response
}

/**
 * Every write of the screen goes through here. It sends the version the screen last saw, keeps
 * the cached version in step with the answer, reloads what changed, and says in words what the
 * refusals mean: 409 (the booklet moved on: reload and repeat), 423 (the lock is gone) and 422
 * (the rules refused the edit).
 */
export function useGuardedEdit(bookletId: string, reopen: () => Promise<void>) {
  const queryClient = useQueryClient()
  const toast = useToast()

  const version = useCallback(
    () => queryClient.getQueryData<Review>(reviewKey(bookletId))?.version ?? 0,
    [queryClient, bookletId],
  )

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: reviewKey(bookletId) }),
      queryClient.invalidateQueries({ queryKey: ['segments', bookletId] }),
      queryClient.invalidateQueries({ queryKey: ['page-text', bookletId] }),
      queryClient.invalidateQueries({ queryKey: ['student-diagrams', bookletId] }),
      queryClient.invalidateQueries({ queryKey: ['booklet', bookletId] }),
    ])
  }, [queryClient, bookletId])

  /** Runs one write. Returns the reply's data, or undefined after telling the teacher why not. */
  const run = useCallback(
    async <T extends object>(
      send: (version: number) => Promise<Reply<T>>,
      failure: string,
    ): Promise<T | undefined> => {
      try {
        const { data, error, response } = await send(version())
        if (data) {
          const moved = (data as { booklet_version?: number }).booklet_version
          if (moved !== undefined) {
            queryClient.setQueryData<Review>(reviewKey(bookletId), (r) =>
              r ? { ...r, version: moved } : r,
            )
          }
          await refresh()
          return data
        }
        if (response.status === 409) {
          toast.show(
            `${problemOf(error, 'The booklet changed.').message} The screen was reloaded: check it and repeat the change.`,
            'amber',
          )
          await refresh()
        } else if (response.status === 423) {
          toast.show(
            'You no longer have this booklet open. Reopening it: repeat the change.',
            'amber',
          )
          await reopen()
          await refresh()
        } else {
          toast.show(problemOf(error, failure).message, 'red')
        }
      } catch {
        toast.show(NETWORK_PROBLEM, 'red')
      }
      return undefined
    },
    [queryClient, bookletId, version, refresh, toast, reopen],
  )

  return { run, refresh, version }
}

export interface RescoreEntry {
  answerId: string
  label: string
  before: number | null
  after: number | null
  max: number
  /** A new suggestion is on its way. */
  pending: boolean
}

interface Seen {
  suggestion: string | null
  mark: number | null
  pending: boolean
}

interface LogState {
  review: Review | undefined
  seen: Map<string, Seen>
  entries: RescoreEntry[]
}

function entry(a: ReviewAnswer, before: number | null): RescoreEntry {
  return {
    answerId: a.id,
    label: a.slot_label,
    before,
    after: a.suggestion?.mark ?? null,
    max: a.max_marks,
    pending: a.rescore_pending,
  }
}

/** The log after a new review arrived: what changed since the one before it. */
function advance(state: LogState, review: Review): LogState {
  const seen = new Map<string, Seen>()
  const updates: RescoreEntry[] = []
  for (const a of review.answers) {
    const now = {
      suggestion: a.suggestion?.id ?? null,
      mark: a.suggestion?.mark ?? null,
      pending: a.rescore_pending,
    }
    seen.set(a.id, now)
    const was = state.seen.get(a.id)
    if (!was) continue
    const started = now.pending && !was.pending
    const arrived = now.suggestion !== was.suggestion
    if (started || arrived) updates.push(entry(a, was.mark))
  }
  const entries = [...state.entries]
  for (const u of updates) {
    const at = entries.findIndex((e) => e.answerId === u.answerId)
    const old = at >= 0 ? entries[at] : undefined
    // Keep the mark the answer had before the first of a run of edits.
    const merged = old && old.pending ? { ...u, before: old.before } : u
    if (at >= 0) entries[at] = merged
    else entries.push(merged)
  }
  return { review, seen, entries }
}

/**
 * What the re-scoring after an edit did, read from the review as it changes: an answer that
 * starts waiting for a new suggestion, or whose suggestion changed, is listed with the mark it
 * had and the mark it has now. The server sets "waiting" in the same transaction as the edit,
 * so no edit's answer ids need to be passed in.
 */
export function useRescoreLog(review: Review | undefined) {
  const [state, setState] = useState<LogState>({ review: undefined, seen: new Map(), entries: [] })
  // Derived while rendering (not in an effect): the log follows the review it was last given.
  if (review && review !== state.review) setState(advance(state, review))

  // An entry shows its answer's current state.
  const entries = state.entries.map((e) => {
    const a = review?.answers.find((x) => x.id === e.answerId)
    return a
      ? { ...e, pending: a.rescore_pending, after: a.suggestion?.mark ?? null, max: a.max_marks }
      : e
  })
  const dismiss = (answerId: string) =>
    setState((s) => ({ ...s, entries: s.entries.filter((e) => e.answerId !== answerId) }))
  return { entries, dismiss }
}
