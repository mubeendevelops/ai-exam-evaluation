import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { STATUS } from '../evaluate/model'

export type RowStatus = components['schemas']['BookletOut']['row_status']
export type Delegate = components['schemas']['DelegateOut']

/** The student list's page size (D217). */
export const PAGE_SIZE = 25

export interface RowQuery {
  q?: string
  status?: RowStatus | ''
  /** From 0. */
  page?: number
  /** Every booklet of the unit (the Submit dialog), no paging. */
  all?: boolean
}

/** One unit with a page of its booklets and each one's approved / attempted answers (P32: one
 * call). Re-read while the machine works on any booklet on the page. */
export function useUnitDetail(unitId: string, query: RowQuery = {}) {
  const q = query.q?.trim() ?? ''
  const status = query.status ?? ''
  const page = query.page ?? 0
  return useQuery({
    queryKey: ['unit', unitId, query.all ? 'all' : { q, status, page }],
    enabled: unitId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/{unit_id}', {
        params: {
          path: { unit_id: unitId },
          query: query.all
            ? {}
            : {
                ...(q ? { q } : {}),
                ...(status ? { status: [status] } : {}),
                offset: page * PAGE_SIZE,
                limit: PAGE_SIZE,
              },
        },
      })
      if (!data) throw new Error('unit unavailable')
      return data
    },
    retry: false,
    // Coming back from step 3 the counts have moved: always read them again on arrival.
    refetchOnMount: 'always',
    refetchInterval: (r) =>
      r.state.data?.booklets.some((b) => STATUS[b.status].working) ? 4000 : false,
  })
}

/** Whom the owner may suggest when asking to move the unit (names only, never themselves). */
export function useDelegates(unitId: string) {
  return useQuery({
    queryKey: ['delegates', unitId],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/{unit_id}/delegates', {
        params: { path: { unit_id: unitId } },
      })
      if (!data) throw new Error('delegates unavailable')
      return data
    },
  })
}
