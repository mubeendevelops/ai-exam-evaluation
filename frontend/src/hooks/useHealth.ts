import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { components } from '../api/schema'

export type Health = components['schemas']['HealthOut']
export type HealthState = { phase: 'loading' } | { phase: 'down' } | { phase: 'up'; health: Health }

const REFRESH_MS = 15_000

/** API and worker health, polled; the status pill and the sign-in card read it. */
export function useHealth(): HealthState {
  const query = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/health')
      if (!data) throw new Error('health unavailable')
      return data
    },
    refetchInterval: REFRESH_MS,
    retry: false,
  })
  if (query.data) return { phase: 'up', health: query.data }
  if (query.isError) return { phase: 'down' }
  return { phase: 'loading' }
}
