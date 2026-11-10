import { describe, expect, it } from 'vitest'
import { editorUrl, returnUrl, safeReturn } from './editorLinks'

describe('safeReturn (P35)', () => {
  it('follows only paths inside the SME portal', () => {
    expect(safeReturn('/sme/papers/p-1')).toBe('/sme/papers/p-1')
    expect(safeReturn('/sme/papers/p-1?reveal=k')).toBe('/sme/papers/p-1?reveal=k')
    expect(safeReturn('/sme')).toBe('/sme')
  })

  it.each([
    null,
    '',
    'https://example.test/sme/papers',
    '//example.test/sme',
    '/smeX/papers',
    '/admin',
    '/sme//example.test',
    '/sme/papers\\..\\x',
    '/sme/papers\nSet-Cookie: x',
  ])('refuses %j', (raw) => {
    expect(safeReturn(raw)).toBeNull()
  })
})

describe('editor addresses', () => {
  it('builds the new and the saved question with the context that is set', () => {
    expect(editorUrl(null)).toBe('/sme/questions/new')
    expect(editorUrl('q-1')).toBe('/sme/questions/q-1')
    const url = editorUrl(null, {
      subject: 's-1',
      marks: '2',
      return: '/sme/papers/p-1',
      slot: 'k-1',
    })
    const [path, query] = url.split('?')
    expect(path).toBe('/sme/questions/new')
    expect(Object.fromEntries(new URLSearchParams(query))).toEqual({
      subject: 's-1',
      marks: '2',
      return: '/sme/papers/p-1',
      slot: 'k-1',
    })
    expect(editorUrl('q-1', { return: null, slot: undefined })).toBe('/sme/questions/q-1')
  })

  it('returns to the paper, naming the question to link', () => {
    expect(returnUrl('/sme/papers/p-1')).toBe('/sme/papers/p-1')
    expect(returnUrl('/sme/papers/p-1', { questionId: 'q-9', slot: 'k-1' })).toBe(
      '/sme/papers/p-1?linked=q-9&slot=k-1',
    )
    expect(returnUrl('/sme/papers/p-1?reveal=k', { questionId: 'q-9', slot: 'k-1' })).toBe(
      '/sme/papers/p-1?reveal=k&linked=q-9&slot=k-1',
    )
  })
})
