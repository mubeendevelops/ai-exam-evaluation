import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'

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
