import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import type { Tone } from '../../components/ui'

export type Account = components['schemas']['AccountOut']
export type Subject = components['schemas']['SubjectOut']
export type Student = components['schemas']['StudentOut']

/** The college's accounts (the admin's own row included; the directory shows SMEs only). */
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/accounts')
      if (!data) throw new Error('accounts unavailable')
      return data
    },
  })
}

/** The admin sees the whole subject list, retired subjects included (D181). */
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

export function isLocked(account: Account): boolean {
  return account.locked_until !== null && new Date(account.locked_until) > new Date()
}

/** The subjects granted to an SME (the schema marks the list optional). */
export function grantsOf(account: Account): readonly string[] {
  return account.subject_ids ?? []
}

export type WorkUnit = components['schemas']['UnitOut']
export type WorkUnitDetail = components['schemas']['UnitDetailOut']
export type UnitPaper = components['schemas']['UnitChoicePaperOut']
export type UnitSme = components['schemas']['UnitSmeOut']
export type UploadedFile = components['schemas']['UploadFileOut']

export type UnitState = WorkUnit['state']

export interface UnitFilter {
  subject_id?: string
  owner_id?: string
  state?: UnitState
}

/** P28 (D164): the SME submits a unit to the admin, the Governor freezes it. */
export const UNIT_STATE: Record<UnitState, { label: string; tone: Tone }> = {
  open: { label: 'Open', tone: 'cyan' },
  submitted: { label: 'Submitted', tone: 'amber' },
  frozen: { label: 'Frozen', tone: 'emerald' },
}

/** Open again after the admin returned it: the admin's note (empty otherwise). */
export function returnNote(unit: WorkUnit): string {
  return unit.state === 'open' && unit.submission?.returned_at ? unit.submission.return_note : ''
}

/** Units of work (P26): every unit for the admin, the SME's own for an SME, the submitted and
 * frozen ones for the Governor (P28). */
export function useUnits(filter: UnitFilter = {}) {
  return useQuery({
    queryKey: ['units', filter],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units', { params: { query: filter } })
      if (!data) throw new Error('units unavailable')
      return data
    },
  })
}

/** "U-0001 · Exam code · 2024" */
export function unitLabel(unit: WorkUnit): string {
  return `${unit.code} · ${unit.paper.exam_code || unit.paper.title} · ${unit.year}`
}

export type DelegationRequest = components['schemas']['DelegationRequestOut']
export type DelegationState = DelegationRequest['state']
export type UnitTransfer = components['schemas']['TransferOut']

/** The delegation inbox (P27): every request for the admin, the SME's own for an SME. */
export function useDelegationRequests(state?: DelegationState) {
  return useQuery({
    queryKey: ['delegation-requests', state ?? 'all'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/delegation-requests', {
        params: { query: state ? { state } : {} },
      })
      if (!data) throw new Error('requests unavailable')
      return data
    },
  })
}

/** Active SMEs entitled to a subject: who may own (or receive) its units (D160). */
export function useEntitledSmes(subjectId: string) {
  return useQuery({
    queryKey: ['unit-smes', subjectId],
    enabled: subjectId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/smes', {
        params: { query: { subject_id: subjectId } },
      })
      if (!data) throw new Error('SMEs unavailable')
      return data
    },
  })
}

export const REQUEST_STATE: Record<DelegationState, { label: string; tone: Tone }> = {
  pending: { label: 'Pending', tone: 'amber' },
  done: { label: 'Transferred', tone: 'emerald' },
  declined: { label: 'Declined', tone: 'red' },
  withdrawn: { label: 'Withdrawn', tone: 'gray' },
}
