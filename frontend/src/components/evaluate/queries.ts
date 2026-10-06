import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import { leafLabels, type PageText } from '../../lib/segments'
import { STATUS, type BookletDetail } from './model'

export type Review = components['schemas']['ReviewOut']
export type ReviewAnswer = components['schemas']['ReviewAnswerOut']
export type StudentDiagram = components['schemas']['StudentDiagramOut']
export type SegmentsOut = components['schemas']['SegmentsOut']

export const reviewKey = (id: string) => ['review', id] as const

export function useBookletDetail(id: string) {
  return useQuery({
    queryKey: ['booklet', id],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: id } },
      })
      if (!data) throw new Error('booklet unavailable')
      return data
    },
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && STATUS[status].working ? 3000 : false
    },
  })
}

/** What OCR read on every page that has been read, by page number. */
export function usePageTexts(booklet: BookletDetail | undefined) {
  const pages = (booklet?.pages ?? []).filter((p) => p.text_read)
  const results = useQueries({
    queries: pages.map((p) => ({
      queryKey: ['page-text', booklet?.id, p.number],
      queryFn: async () => {
        const { data } = await api.GET('/api/v1/booklets/{booklet_id}/pages/{number}/text', {
          params: { path: { booklet_id: booklet?.id ?? '', number: p.number } },
        })
        if (!data) throw new Error('page text unavailable')
        return data
      },
    })),
  })
  const texts = new Map<number, PageText>()
  results.forEach((r, k) => {
    const page = pages[k]
    if (r.data && page) texts.set(page.number, r.data)
  })
  return {
    texts,
    loading: results.some((r) => r.isPending),
    failed: results.some((r) => r.isError),
  }
}

export function useSegments(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ['segments', id],
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}/segments', {
        params: { path: { booklet_id: id } },
      })
      if (!data) throw new Error('segments unavailable')
      return data
    },
  })
}

/** The review view. While a new suggestion is on its way (or `poll` is set) it is re-read often. */
export function useReview(id: string, enabled: boolean, poll: boolean) {
  return useQuery({
    queryKey: reviewKey(id),
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}/review', {
        params: { path: { booklet_id: id } },
      })
      if (!data) throw new Error('review unavailable')
      return data
    },
    refetchInterval: (query) =>
      poll || query.state.data?.answers.some((a) => a.rescore_pending) ? 1500 : false,
  })
}

export function useStudentDiagrams(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ['student-diagrams', id],
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}/diagrams', {
        params: { path: { booklet_id: id } },
      })
      if (!data) throw new Error('diagrams unavailable')
      return data
    },
  })
}

/** The question labels of the booklet's exam, for "reassign". */
export function useExamLabels(blueprintId: string | undefined) {
  return useQuery({
    queryKey: ['blueprint-labels', blueprintId],
    enabled: !!blueprintId,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/blueprints/{blueprint_id}', {
        params: { path: { blueprint_id: blueprintId ?? '' } },
      })
      if (!data) throw new Error('exam unavailable')
      return leafLabels(data.document)
    },
    staleTime: 5 * 60_000,
  })
}

// --- the booklet lock ------------------------------------------------------------------------

export type LockState =
  | { kind: 'idle' }
  | { kind: 'mine' }
  | { kind: 'other'; holder: string | null; until: string | null }
  | { kind: 'error'; message: string }

const REFRESH_MS = 4 * 60_000
const RELEASE_DELAY_MS = 400
const releasing = new Map<string, ReturnType<typeof setTimeout>>()

/**
 * Opens the booklet for review while the screen is shown (the lock every write needs, P15):
 * takes it, keeps it fresh and gives it back on leaving. The release waits a moment so that a
 * quick remount (React StrictMode, a refresh of the route) does not release a lock it is about
 * to take again.
 */
export function useBookletLock(id: string, enabled: boolean) {
  const queryClient = useQueryClient()
  const mine = useRef(false)

  const open = useMutation({
    mutationFn: async (): Promise<LockState> => {
      const pending = releasing.get(id)
      if (pending !== undefined) {
        clearTimeout(pending)
        releasing.delete(id)
      }
      try {
        const { data, error, response } = await api.POST('/api/v1/booklets/{booklet_id}/lock', {
          params: { path: { booklet_id: id } },
        })
        if (data) {
          mine.current = true
          queryClient.setQueryData(reviewKey(id), data)
          await queryClient.invalidateQueries({ queryKey: ['booklet', id] })
          return { kind: 'mine' }
        }
        mine.current = false
        if (response.status === 423) {
          const held = error as { expires_at?: string | null } | undefined
          const view = await queryClient.fetchQuery({
            queryKey: reviewKey(id),
            staleTime: 0,
            queryFn: async () => {
              const { data: review } = await api.GET('/api/v1/booklets/{booklet_id}/review', {
                params: { path: { booklet_id: id } },
              })
              if (!review) throw new Error('review unavailable')
              return review
            },
          })
          return {
            kind: 'other',
            holder: view.lock?.holder_name ?? null,
            until: held?.expires_at ?? view.lock?.expires_at ?? null,
          }
        }
        return { kind: 'error', message: problemOf(error, 'Could not open the booklet.').message }
      } catch {
        return { kind: 'error', message: 'Could not reach the server to open the booklet.' }
      }
    },
  })
  const { mutate, mutateAsync } = open

  useEffect(() => {
    if (!enabled) return
    mutate()
    const refresh = setInterval(() => {
      if (mine.current) mutate()
    }, REFRESH_MS)
    return () => {
      clearInterval(refresh)
      if (!mine.current) return
      const timer = setTimeout(() => {
        releasing.delete(id)
        void api.DELETE('/api/v1/booklets/{booklet_id}/lock', {
          params: { path: { booklet_id: id } },
        })
      }, RELEASE_DELAY_MS)
      releasing.set(id, timer)
    }
  }, [enabled, id, mutate])

  const reopen = useCallback(async () => {
    await mutateAsync()
  }, [mutateAsync])
  const lock: LockState = open.data ?? { kind: 'idle' }
  return { lock, reopen }
}
