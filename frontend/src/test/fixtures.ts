import type { components } from '../api/schema'

type User = components['schemas']['UserOut']
type Me = components['schemas']['MeOut']

export const COLLEGE_ID = '11111111-1111-4111-8111-111111111111'

export function userOf(overrides: Partial<User> = {}): User {
  return {
    id: '22222222-2222-4222-8222-222222222222',
    college_id: COLLEGE_ID,
    display_name: 'Asha Rao',
    email: 'asha@college.test',
    role: 'teacher',
    active: true,
    ...overrides,
  }
}

export function meOf(overrides: Partial<User> = {}, left = 10): Me {
  return {
    user: userOf(overrides),
    institution_id: 'SYNTH_COLLEGE',
    college_name: 'Synthetic College',
    recovery_codes_left: left,
  }
}

export function tokenOf(overrides: Partial<User> = {}, access = 'access-1') {
  return {
    status: 'signed_in' as const,
    access_token: access,
    token_type: 'bearer' as const,
    expires_in: 900,
    user: userOf(overrides),
  }
}

export const healthy = {
  status: 'ok',
  version: '0.0.0',
  environment: 'test',
  device: { kind: 'cpu', name: 'cpu', detail: 'CPU selected' },
  worker: { status: 'up', detail: 'Worker running' },
}

/** A TBF1 Bloom filter (see tarn_adapters/auth/passwords.py) holding `words`. */
export async function bloomOf(words: string[], m = 4096, k = 4): Promise<ArrayBuffer> {
  const bits = new Uint8Array(Math.ceil(m / 8))
  for (const word of words) {
    const digest = new DataView(
      await crypto.subtle.digest('SHA-256', new TextEncoder().encode(word.toLowerCase())),
    )
    const h1 = digest.getUint32(0, false)
    const h2 = (digest.getUint32(4, false) | 1) >>> 0
    for (let i = 0; i < k; i++) {
      const position = Number((BigInt(h1) + BigInt(i) * BigInt(h2)) % BigInt(m))
      bits[position >> 3] = (bits[position >> 3] as number) | (1 << (position & 7))
    }
  }
  const out = new Uint8Array(9 + bits.length)
  out.set([0x54, 0x42, 0x46, 0x31])
  new DataView(out.buffer).setUint32(4, m, false)
  out[8] = k
  out.set(bits, 9)
  return out.buffer
}
