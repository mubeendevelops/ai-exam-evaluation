import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { components } from '../api/schema'
import type { BlueprintDocument } from '../lib/blueprint'
import { useDebounced } from './useDebounced'

export type Check = components['schemas']['ValidationOut']
export type CheckState = 'checking' | 'valid' | 'invalid' | 'unavailable'

const WAIT_MS = 300

/**
 * Asks the server whether the document is a valid blueprint (`POST /blueprints/validate`), a
 * moment after the last change. The server is the one place the rules live; until its answer
 * for *this* document arrives the state is "checking".
 */
export function useBlueprintCheck(document: BlueprintDocument): {
  state: CheckState
  check: Check | undefined
} {
  const text = JSON.stringify(document)
  const settled = useDebounced(text, WAIT_MS)
  const query = useQuery({
    queryKey: ['blueprint-check', settled],
    queryFn: async () => {
      const { data } = await api.POST('/api/v1/blueprints/validate', {
        body: JSON.parse(settled) as Record<string, unknown>,
      })
      if (!data) throw new Error('validation unavailable')
      return data
    },
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  })
  if (settled === text && query.isError) return { state: 'unavailable', check: undefined }
  if (settled !== text || query.isFetching || !query.data)
    return { state: 'checking', check: query.data }
  return { state: query.data.valid ? 'valid' : 'invalid', check: query.data }
}
