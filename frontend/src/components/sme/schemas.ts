import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'

export type SchemaCard = components['schemas']['SchemaCardOut']

/** Manage Schema's cards (P33): the schemas of my subjects matching the filters, and every
 * batch in use for the filter (the server filters; `batches` is never filtered). */
export function useSchemas(q = '', batch = '') {
  return useQuery({
    queryKey: ['schemas', { q, batch }],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/schemas', {
        params: { query: { ...(q ? { q } : {}), ...(batch ? { target_batch: batch } : {}) } },
      })
      if (!data) throw new Error('schemas unavailable')
      return data
    },
    placeholderData: keepPreviousData,
  })
}

/** My subjects (the admin grants them, D160), shared with the designer's subject picker. */
export function useSubjects() {
  return useQuery({
    queryKey: ['subjects'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/subjects')
      if (!data) throw new Error('subjects unavailable')
      return data
    },
  })
}

const month = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })

/** "Oct 2026", as the mock's cards say; empty when unknown. */
export function updatedOf(at: string | null | undefined): string {
  if (!at) return ''
  const date = new Date(at)
  return Number.isNaN(date.getTime()) ? '' : month.format(date)
}

/** Where a paper opens: the designer in paper mode (P34). */
export function paperEditorPath(id: string): string {
  return `/sme/papers/${id}`
}
