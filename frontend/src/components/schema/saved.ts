import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import {
  fromDocument,
  toDocument,
  type BlueprintDocument,
  type FormState,
} from '../../lib/blueprint'
import { useToast } from '../ui'
import { useVerdict } from './Designer'

export type Blueprint = components['schemas']['BlueprintOut']

/** One saved blueprint, newest version (P33: the designer loads it, closing O20). */
export function useSavedBlueprint(id: string) {
  return useQuery({
    queryKey: ['blueprint', id],
    enabled: id !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/blueprints/{blueprint_id}', {
        params: { path: { blueprint_id: id } },
      })
      if (!data) throw new Error('blueprint unavailable')
      return data
    },
    retry: false,
  })
}

export function formOf(blueprint: Blueprint): FormState {
  return fromDocument(blueprint.document as unknown as BlueprintDocument)
}

/** The JSON a form saves, to tell unsaved changes apart. */
export function jsonOf(form: FormState): string {
  return JSON.stringify(toDocument(form), null, 2)
}

/** Lists that show blueprints, read again after a save. */
async function refreshLists(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['blueprints'] }),
    queryClient.invalidateQueries({ queryKey: ['schemas'] }),
  ])
}

/**
 * The designer's state for a saved blueprint (or a new one when `saved` is null): the form,
 * the server's verdict, whether it differs from what was saved, and `save` (the next version,
 * or with `asNew` another blueprint). `save` resolves to what the server saved, or null after
 * telling the user why not.
 */
export function useBlueprintEditor(saved: Blueprint | null, initial: () => FormState) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormState>(() => (saved ? formOf(saved) : initial()))
  const [savedJson, setSavedJson] = useState(() => (saved ? jsonOf(formOf(saved)) : ''))
  const [version, setVersion] = useState(saved?.version ?? 0)
  const [busy, setBusy] = useState(false)
  const verdict = useVerdict(form)
  const dirty = !saved || savedJson !== verdict.json

  async function save(asNew = false): Promise<Blueprint | null> {
    setBusy(true)
    try {
      const body = verdict.document as unknown as Record<string, unknown>
      const { data, error } =
        saved && !asNew
          ? await api.PUT('/api/v1/blueprints/{blueprint_id}', {
              params: { path: { blueprint_id: saved.id } },
              body,
            })
          : await api.POST('/api/v1/blueprints', { body })
      if (!data) {
        toast.show(problemOf(error, 'Could not save it.').message, 'red')
        return null
      }
      queryClient.setQueryData(['blueprint', data.id], data)
      await refreshLists(queryClient)
      toast.show(`${data.title} saved as version ${data.version}.`)
      if (data.id === saved?.id) {
        const next = formOf(data)
        setForm(next)
        setSavedJson(jsonOf(next))
        setVersion(data.version)
      }
      return data
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
      return null
    } finally {
      setBusy(false)
    }
  }

  return { form, setForm, verdict, dirty, version, busy, save }
}
