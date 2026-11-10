import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'

export type Notice = components['schemas']['NotificationOut']
export type SearchResults = components['schemas']['SearchOut']

/** How often the bell asks for the unread count while the tab is visible (P37). */
export const POLL_MS = 60_000
export const PANEL_SIZE = 20

/** Whether the tab is shown; the bell stops asking while it is hidden. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}

export function useUnreadCount() {
  const visible = usePageVisible()
  return useQuery({
    queryKey: ['notifications', 'unread'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/notifications/unread-count')
      if (!data) throw new Error('notifications unavailable')
      return data.unread
    },
    enabled: visible,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    retry: false,
  })
}

export function useNotifications(open: boolean) {
  return useQuery({
    queryKey: ['notifications', 'list'],
    enabled: open,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/notifications', {
        params: { query: { limit: PANEL_SIZE } },
      })
      if (!data) throw new Error('notifications unavailable')
      return data
    },
    refetchOnMount: 'always',
    retry: false,
  })
}

export function useMarkRead() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (id: string | 'all') => {
      if (id === 'all') {
        await api.POST('/api/v1/notifications/read-all')
      } else {
        await api.POST('/api/v1/notifications/{notification_id}/read', {
          params: { path: { notification_id: id } },
        })
      }
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['notifications'] }),
  })
}

/** Where a notice leads: the unit while it is still the SME's, My Assignments otherwise. */
export function targetOf(notice: Notice): string {
  switch (notice.kind) {
    case 'transferred_away':
    case 'request_done':
      return '/sme'
    case 'request_declined':
    case 'assigned':
    case 'returned':
    case 'due_soon':
    case 'overdue':
      return `/sme/units/${notice.unit_id}`
  }
}

function dueIn(due: string | null | undefined, today: string): string {
  if (!due) return 'soon'
  const days = Math.round((Date.parse(due) - Date.parse(today)) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'tomorrow'
  return `in ${days} days`
}

/** The notice as a sentence. ``today`` is the UTC date (due dates are UTC dates, D209). */
export function sentenceOf(notice: Notice, today: string): string {
  const unit = notice.unit_code
  switch (notice.kind) {
    case 'assigned':
      return `Unit ${unit} was assigned to you${notice.due_date ? `, due ${notice.due_date}` : ''}.`
    case 'transferred_away':
      return `Unit ${unit} was moved to another SME by your admin.`
    case 'returned':
      return `Your admin returned unit ${unit}${notice.note ? `: “${notice.note}”` : '.'}`
    case 'request_done':
      return `Your reassignment request for ${unit} was done: the unit moved to another SME.`
    case 'request_declined':
      return `Your reassignment request for ${unit} was declined. The note is under My reassignment requests.`
    case 'due_soon':
      return `Unit ${unit} is due ${dueIn(notice.due_date, today)} (${notice.due_date ?? ''}).`
    case 'overdue':
      return `Unit ${unit} is overdue: it was due ${notice.due_date ?? ''}.`
  }
}

export function useSearch(text: string) {
  const q = text.trim()
  return useQuery({
    queryKey: ['search', q],
    enabled: q.length >= 2 && q.length <= 80,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/search', { params: { query: { q } } })
      if (!data) throw new Error('search unavailable')
      return data
    },
    staleTime: 30_000,
    retry: false,
  })
}
