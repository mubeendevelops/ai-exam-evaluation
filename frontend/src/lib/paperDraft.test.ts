import { afterEach, describe, expect, it, vi } from 'vitest'
import { initialForm } from './blueprint'
import { clearDraft, loadDraft, saveDraft } from './paperDraft'

afterEach(() => vi.restoreAllMocks())

describe('a paper’s unsaved draft (P35)', () => {
  it('is kept per paper and version, and cleared once restored', () => {
    const form = { ...initialForm('paper'), title: 'Draft title' }
    saveDraft('p-1', 3, form)
    expect(loadDraft('p-1', 3)?.title).toBe('Draft title')
    expect(loadDraft('p-1', 4)).toBeNull() // the paper was saved since: the draft is stale
    expect(loadDraft('p-2', 3)).toBeNull()
    clearDraft('p-1')
    expect(loadDraft('p-1', 3)).toBeNull()
  })

  it('never throws when storage is blocked or holds rubbish', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => saveDraft('p-1', 1, initialForm('paper'))).not.toThrow()
    vi.restoreAllMocks()
    globalThis.sessionStorage.setItem('tarn.paper-draft.p-9', '{not json')
    expect(loadDraft('p-9', 1)).toBeNull()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(loadDraft('p-1', 1)).toBeNull()
    expect(() => clearDraft('p-1')).not.toThrow()
  })
})
