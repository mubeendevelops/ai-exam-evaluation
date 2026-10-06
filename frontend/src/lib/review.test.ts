import { describe, expect, it } from 'vitest'
import { answer } from '../test/evaluateFixtures'
import {
  cleanTag,
  loadCustomTags,
  markProblem,
  neighbour,
  nextOpen,
  parseComparison,
  progressOf,
  saveCustomTags,
  slotInfo,
  stageOf,
} from './review'

describe('slotInfo', () => {
  it('maps labels to questions and marks, through parts and OR alternatives', () => {
    const slots = slotInfo({
      sections: [
        {
          label: 'A',
          items: [
            { type: 'question', label: '1', marks: 2, question_id: 'q-1' },
            {
              type: 'question',
              label: '2',
              marks: 8,
              parts: [
                { label: 'a', marks: 5, question_id: 'q-2a' },
                { label: 'b', marks: 3 },
              ],
            },
          ],
        },
        {
          label: 'B',
          items: [
            {
              type: 'or',
              alternatives: [
                { label: '3', marks: 10, question_id: 'q-3' },
                { label: '4', marks: 10 },
              ],
            },
          ],
        },
      ],
    })
    expect([...slots.keys()]).toEqual(['1', '2.a', '2.b', '3', '4'])
    expect(slots.get('1')).toEqual({ label: '1', questionId: 'q-1', marks: 2, sectionLabel: 'A' })
    expect(slots.get('2.a')?.questionId).toBe('q-2a')
    expect(slots.get('2.b')?.questionId).toBeNull()
    expect(slots.get('3')?.sectionLabel).toBe('B')
    expect(slots.get('4')?.questionId).toBeNull()
  })

  it('gives nothing for a document without sections', () => {
    expect(slotInfo({}).size).toBe(0)
  })
})

describe('progress and moving on', () => {
  const list = [
    answer('a1', '1', 1, { status: 'approved' }),
    answer('a2', '2', 1, { status: 'skipped' }),
    answer('a3', '3', null, { attempted: false }),
    answer('a4', '4', 1),
    answer('a5', '5', 1, {
      status: 'suggested',
      draft: { amendment_id: 'x', reason: '', opened_by: 'u', opened_at: '' },
    }),
  ]

  it('names the stage of an answer', () => {
    expect(list.map(stageOf)).toEqual(['approved', 'skipped', 'none', 'todo', 'draft'])
  })

  it('counts the answers with text', () => {
    expect(progressOf(list)).toEqual({ attempted: 4, approved: 1, skipped: 1, drafts: 1 })
  })

  it('moves to the neighbours that have text, and stops at the ends', () => {
    expect(neighbour(list, '2', 1)?.slot_label).toBe('4')
    expect(neighbour(list, '4', -1)?.slot_label).toBe('2')
    expect(neighbour(list, '1', -1)).toBeUndefined()
    expect(neighbour(list, '5', 1)).toBeUndefined()
  })

  it('finds the next open answer, wrapping round to a skipped one', () => {
    expect(nextOpen(list, null)?.slot_label).toBe('2')
    expect(nextOpen(list, '2')?.slot_label).toBe('4')
    expect(nextOpen(list, '5')?.slot_label).toBe('2')
    expect(nextOpen([answer('a1', '1', 1, { status: 'approved' })], '1')).toBeUndefined()
    expect(nextOpen([answer('a1', '1', 1)], '1')).toBeUndefined()
  })
})

describe('markProblem', () => {
  it('accepts marks in the paper’s step within the question’s marks', () => {
    expect(markProblem('2.5', 0.5, 3)).toBeNull()
    expect(markProblem('0', 0.5, 3)).toBeNull()
    expect(markProblem('3', 0.5, 3)).toBeNull()
    expect(markProblem('0.3', 0.1, 1)).toBeNull()
  })

  it('refuses the rest in words', () => {
    expect(markProblem('', 0.5, 3)).toMatch(/Enter a mark/)
    expect(markProblem('abc', 0.5, 3)).toMatch(/number/)
    expect(markProblem('3.5', 0.5, 3)).toMatch(/between 0 and 3/)
    expect(markProblem('-1', 0.5, 3)).toMatch(/between 0 and 3/)
    expect(markProblem('2.25', 0.5, 3)).toMatch(/steps of 0.5/)
  })
})

describe('tags', () => {
  it('cleans a tag', () => {
    expect(cleanTag('  Cite   the law  ')).toBe('Cite the law')
    expect(cleanTag('x'.repeat(60))).toHaveLength(40)
  })

  it('keeps the teacher’s own tags in the browser', () => {
    localStorage.clear()
    expect(loadCustomTags()).toEqual([])
    saveCustomTags(['Mine'])
    expect(loadCustomTags()).toEqual(['Mine'])
    localStorage.setItem('tarn.feedback-tags', '{not json')
    expect(loadCustomTags()).toEqual([])
  })
})

describe('parseComparison', () => {
  it('reads the parts the overlay draws, and refuses other documents', () => {
    const c = parseComparison({
      student_diagram_id: 'd-1',
      similarity: 0.5,
      sub_scores: { nodes: 1, edges: 0, labels: 1 },
      nodes: [{ id: 's1' }],
      reference_nodes: [],
      edges: [],
    })
    expect(c?.student_diagram_id).toBe('d-1')
    expect(c?.nodes).toHaveLength(1)
    expect(parseComparison({ nodes: 'x' })).toBeNull()
  })
})
