import { describe, expect, it } from 'vitest'
import { detail } from '../test/qnaFixtures'
import { seedFrom } from './questionSeed'

describe('seedFrom: a question cloned as the start of a new one (P35)', () => {
  it('copies the text, topic, difficulty, first key and rubric, never the code or ids', () => {
    const seed = seedFrom(detail())
    expect(seed).toMatchObject({
      subjectId: detail().subject_id,
      marks: '4',
      text: 'State Lenz’s law.',
      category: 'Electromagnetism',
      difficulty: 'easy',
      answer: 'The induced emf opposes the change of flux.',
    })
    expect(seed.criteria?.map((c) => [c.type, c.label, c.weight, c.id])).toEqual([
      ['list', 'Names the effect', '2', null],
      ['semantic', 'Explains why', '2', null],
    ])
    expect(seed.note).toBe('Started from PHY-Q1; not copied: its files.')
  })

  it('takes the slot’s marks, and leaves the rubric out when they differ', () => {
    const same = seedFrom(detail(), '4')
    expect(same.criteria).toHaveLength(2)
    const other = seedFrom(detail(), '2')
    expect(other.marks).toBe('2')
    expect(other.criteria).toBeUndefined()
    expect(other.note).toContain('its rubric, because the slot carries other marks')
  })

  it('leaves a diagram criterion out (it needs a diagram of the new question)', () => {
    const q = detail({
      diagrams: [],
      key_files: [],
      rubric: {
        ...detail().rubric,
        criteria: [
          ...detail().rubric.criteria,
          {
            version: 1,
            criterion: {
              id: 'c-3',
              type: 'diagram',
              label: 'Nodes',
              weight: 1,
              params: { reference_diagram_id: 'd-1', component: 'nodes' },
            },
          },
        ],
      },
    })
    const seed = seedFrom(q)
    expect(seed.criteria?.map((c) => c.type)).toEqual(['list', 'semantic'])
    expect(seed.note).toBe('Started from PHY-Q1; not copied: its diagram criterion.')
  })

  it('has no note when everything was copied, and skips a guidance-only key', () => {
    const q = detail({
      diagrams: [],
      key_files: [],
      reference_answers: [
        { id: 'a-0', version: 1, text: 'Mark by hand.', guidance_only: true, synthetic: false },
        { id: 'a-1', version: 1, text: 'The real key.', guidance_only: false, synthetic: false },
      ],
    })
    const seed = seedFrom(q)
    expect(seed.note).toBeUndefined()
    expect(seed.answer).toBe('The real key.')
  })
})
