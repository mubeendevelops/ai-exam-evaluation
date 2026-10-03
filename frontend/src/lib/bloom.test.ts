import { describe, expect, it } from 'vitest'
import fixture from '../test/bloom-fixture.json'
import { bloomOf } from '../test/fixtures'
import { parseBloom } from './bloom'

describe('Bloom filter reader (TBF1)', () => {
  it('finds listed words, any case, and rejects others', async () => {
    const bloom = parseBloom(await bloomOf(['password123', 'qwerty']))
    expect(await bloom.mightContain('password123')).toBe(true)
    expect(await bloom.mightContain('PassWord123')).toBe(true)
    expect(await bloom.mightContain('correct horse battery staple')).toBe(false)
  })

  it('reads a filter built by the server (bloom-fixture.json)', async () => {
    const raw = Uint8Array.from(atob(fixture.bytes), (c) => c.charCodeAt(0))
    const bloom = parseBloom(raw.buffer)
    for (const word of fixture.words) expect(await bloom.mightContain(word)).toBe(true)
    for (const word of fixture.absent) expect(await bloom.mightContain(word)).toBe(false)
  })

  it('derives the same bit positions as the server', async () => {
    // fixture.positions_hello = tarn_adapters.auth.passwords._positions("hello", m=4096, k=4)
    const bits = new Uint8Array(await bloomOf(['hello'])).subarray(9)
    const set = [...bits].flatMap((byte, i) =>
      [...Array(8).keys()].filter((b) => byte & (1 << b)).map((b) => i * 8 + b),
    )
    expect(set.sort((a, b) => a - b)).toEqual([...fixture.positions_hello].sort((a, b) => a - b))
  })

  it('refuses data that is not a TBF1 filter or is cut short', async () => {
    expect(() => parseBloom(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]).buffer)).toThrow(/TBF1/)
    const whole = new Uint8Array(await bloomOf(['x']))
    expect(() => parseBloom(whole.slice(0, 20).buffer)).toThrow(/truncated/)
  })
})
