import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'

export type PaperCard = components['schemas']['PaperCardOut']

/** Cards per page on Manage Question Papers. */
export const PAPERS_PAGE = 12

export interface PaperFilters {
  q: string
  descriptor: string
  /** The creation date, `YYYY-MM-DD` (UTC), or ''. */
  date: string
  /** 1-based. */
  page: number
}

/** Manage Question Papers (P34): one page of the papers of my subjects, the total and every
 * descriptor in use (the server filters; `descriptors` is never filtered). */
export function usePapers({ q, descriptor, date, page }: PaperFilters) {
  return useQuery({
    queryKey: ['papers', { q, descriptor, date, page }],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/papers', {
        params: {
          query: {
            limit: PAPERS_PAGE,
            offset: (page - 1) * PAPERS_PAGE,
            ...(q ? { q } : {}),
            ...(descriptor ? { descriptor } : {}),
            ...(date ? { created_on: date } : {}),
          },
        },
      })
      if (!data) throw new Error('papers unavailable')
      return data
    },
    placeholderData: keepPreviousData,
  })
}

/** Every descriptor in use, for the dialog's suggestions (the first page carries them). */
export function useDescriptors(): string[] {
  return usePapers({ q: '', descriptor: '', date: '', page: 1 }).data?.descriptors ?? []
}

/** The address of one version's question paper PDF (D224). */
export function paperPdfUrl(id: string, version: number): string {
  return `/api/v1/blueprints/${id}/versions/${version}/paper.pdf`
}

/** The file name a downloaded paper gets, as the server names it. */
export function paperFileName(paper: { exam_code: string; title: string; version: number }) {
  const base =
    (paper.exam_code || paper.title)
      .replace(/[^A-Za-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'paper'
  return `paper-${base}-v${paper.version}.pdf`
}

/** "3 Hours", "2.5 Hours", "45 Minutes" as the mock's cards say; '' when not set. */
export function durationOf(minutes: number | null | undefined): string {
  if (!minutes) return ''
  if (minutes < 60) return `${minutes} Minutes`
  const hours = Math.round((minutes / 60) * 100) / 100
  return `${hours} ${hours === 1 ? 'Hour' : 'Hours'}`
}
