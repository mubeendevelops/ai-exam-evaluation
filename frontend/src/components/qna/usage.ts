import type { QueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { components } from '../../api/schema'

export type QuestionUsage = components['schemas']['QuestionUsageOut']

/** Where a question is used, read afresh (never from the cache: papers change under us). */
export function fetchUsage(queryClient: QueryClient, questionId: string): Promise<QuestionUsage> {
  return queryClient.fetchQuery({
    queryKey: ['question-usage', questionId],
    staleTime: 0,
    retry: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions/{question_id}/usage', {
        params: { path: { question_id: questionId } },
      })
      if (!data) throw new Error('usage unavailable')
      return data
    },
  })
}
