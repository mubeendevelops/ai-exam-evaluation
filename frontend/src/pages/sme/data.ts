import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import type { WorkUnit } from '../admin/data'

export type AssignmentSummary = components['schemas']['AssignmentSummaryOut']

/** The SME's own figures (P31, D216): their current units only. */
export function useAssignmentSummary() {
  return useQuery({
    queryKey: ['me', 'assignments', 'summary'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/me/assignments/summary')
      if (!data) throw new Error('summary unavailable')
      return data
    },
  })
}

/** The live subjects granted to the signed-in SME, by name (D160). */
export function useMySubjects() {
  return useQuery({
    queryKey: ['subjects'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/subjects')
      if (!data) throw new Error('subjects unavailable')
      return data
    },
  })
}

/** The mock's "Order By" choices, in its order and words; the value is kept in `?sort=`. */
export const SORTS = [
  { value: 'due-asc', label: 'Due Date (Earliest First)' },
  { value: 'due-desc', label: 'Due Date (Latest First)' },
  { value: 'subject-asc', label: 'Subject (A - Z)' },
  { value: 'subject-desc', label: 'Subject (Z - A)' },
  { value: 'completion-desc', label: 'Completion % (Highest First)' },
  { value: 'completion-asc', label: 'Completion % (Lowest First)' },
] as const

export type Sort = (typeof SORTS)[number]['value']
export const DEFAULT_SORT: Sort = 'due-asc'

export function sortOf(value: string | null): Sort {
  return SORTS.find((s) => s.value === value)?.value ?? DEFAULT_SORT
}

/** Completed booklets as a share of the unit's booklets, 0 to 1 (0 with none). */
export function completionOf(unit: WorkUnit): number {
  return unit.counts.total > 0 ? unit.counts.completed / unit.counts.total : 0
}

/** Units in the chosen order; ties by unit code so the order never jitters. */
export function sortUnits(units: readonly WorkUnit[], sort: Sort): WorkUnit[] {
  const [key, direction] = sort.split('-') as ['due' | 'subject' | 'completion', 'asc' | 'desc']
  const sign = direction === 'asc' ? 1 : -1
  const by = (a: WorkUnit, b: WorkUnit): number => {
    if (key === 'due') return a.due_date.localeCompare(b.due_date)
    if (key === 'subject')
      return a.subject.name.localeCompare(b.subject.name, undefined, { sensitivity: 'base' })
    return completionOf(a) - completionOf(b)
  }
  return [...units].sort((a, b) => sign * by(a, b) || a.code.localeCompare(b.code))
}

/** Today in UTC as `YYYY-MM-DD`: the server's day for "overdue" (D209). */
export function todayUtc(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10)
}

/** An open unit whose due date has passed; a submitted unit is not late (D209). */
export function isOverdue(unit: WorkUnit, today: string = todayUtc()): boolean {
  return unit.state === 'open' && unit.due_date < today
}

/** The open unit due first: where "Start evaluating" goes. */
export function firstOpenUnit(units: readonly WorkUnit[]): WorkUnit | undefined {
  return sortUnits(
    units.filter((u) => u.state === 'open'),
    'due-asc',
  )[0]
}
