import { describe, expect, it } from 'vitest'
import {
  evenWeights,
  fromBody,
  itemsText,
  newCriterion,
  parseItems,
  toBody,
  weightTotal,
} from './rubric'

describe('list items', () => {
  it('reads one item per line with optional synonyms', () => {
    expect(parseItems('alpha | a, first\n\n  beta  \ngamma|')).toEqual([
      { term: 'alpha', synonyms: ['a', 'first'] },
      { term: 'beta', synonyms: [] },
      { term: 'gamma', synonyms: [] },
    ])
  })

  it('writes them back the same way', () => {
    const items = [
      { term: 'alpha', synonyms: ['a', 'first'] },
      { term: 'beta', synonyms: [] },
    ]
    expect(parseItems(itemsText(items))).toEqual(items)
  })
})

describe('criterion <-> request', () => {
  it('builds the list criterion with items, synonyms and the required count', () => {
    const c = {
      ...newCriterion('list', '2'),
      label: ' Names the items ',
      items: 'alpha | a\nbeta',
      required: '2',
    }
    expect(toBody(c)).toEqual({
      id: null,
      label: 'Names the items',
      weight: 2,
      type: 'list',
      params: {
        items: [
          { term: 'alpha', synonyms: ['a'] },
          { term: 'beta', synonyms: [] },
        ],
        required_count: 2,
      },
    })
  })

  it('builds numeric, semantic and diagram criteria', () => {
    expect(
      toBody({
        ...newCriterion('numeric', '1'),
        label: 'Step',
        expected: '2.5',
        tolerance: '0.1',
        unit: 'V',
      }),
    ).toMatchObject({ type: 'numeric', params: { expected: 2.5, tolerance: 0.1, unit: 'V' } })
    expect(
      toBody({ ...newCriterion('semantic', '3'), label: 'Why', statement: 'It scatters.' }),
    ).toMatchObject({ type: 'semantic', params: { reference_statement: 'It scatters.' } })
    expect(
      toBody({
        ...newCriterion('diagram', '1'),
        label: 'Nodes',
        diagramId: 'd-1',
        component: 'nodes',
      }),
    ).toMatchObject({
      type: 'diagram',
      params: { reference_diagram_id: 'd-1', component: 'nodes' },
    })
  })

  it('sends unreadable numbers as typed, so the server can explain them', () => {
    expect(toBody({ ...newCriterion('semantic', 'abc'), label: 'x', statement: 's' }).weight).toBe(
      'abc',
    )
    expect(toBody({ ...newCriterion('semantic', ''), label: 'x', statement: 's' }).weight).toBe('')
  })

  it('round-trips a stored criterion and keeps its id for the next version', () => {
    const stored = {
      id: 'c-1',
      type: 'numeric' as const,
      label: 'Step 1',
      weight: 1.5,
      params: { expected: 7, tolerance: 0.5, unit: '' },
    }
    const form = fromBody(stored)
    expect(form.id).toBe('c-1')
    expect(toBody(form)).toEqual(stored)
  })
})

describe('weights', () => {
  it('adds up what is typed', () => {
    const rows = ['1.5', '2', 'x', ''].map((w) => newCriterion('semantic', w))
    expect(weightTotal(rows)).toBe(3.5)
  })

  it('splits a total evenly, the remainder to the last', () => {
    expect(evenWeights(4, 2)).toEqual(['2', '2'])
    expect(evenWeights(5, 3)).toEqual(['1.66', '1.66', '1.68'])
    expect(evenWeights(5, 0)).toEqual([])
  })
})
