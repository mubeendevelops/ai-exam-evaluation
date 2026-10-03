import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { parseBloom, type Bloom } from '../lib/bloom'
import { strengthOf, type Strength } from '../lib/password'

let cached: Promise<Bloom | null> | null = null

/** Downloads the common-password Bloom filter once per page load (the server caches it a day). */
export function loadBloom(): Promise<Bloom | null> {
  cached ??= api
    .GET('/api/v1/auth/password-bloom', { parseAs: 'arrayBuffer' })
    .then(({ data }) => (data ? parseBloom(data) : null))
    .catch(() => null)
    .then((bloom) => {
      if (!bloom) cached = null // try again next time
      return bloom
    })
  return cached
}

export function resetBloomCache(): void {
  cached = null
}

/** The strength hint for a password being typed. Without a filter, only the length counts. */
export function usePasswordStrength(password: string): Strength {
  const [common, setCommon] = useState<{ password: string; hit: boolean } | null>(null)

  useEffect(() => {
    if (password === '') return
    let cancelled = false
    void loadBloom().then(async (bloom) => {
      const hit = bloom ? await bloom.mightContain(password) : false
      if (!cancelled) setCommon({ password, hit })
    })
    return () => {
      cancelled = true
    }
  }, [password])

  return strengthOf(password, common?.password === password && common.hit)
}
