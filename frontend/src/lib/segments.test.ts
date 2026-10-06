import { describe, expect, it } from 'vitest'
import {
  buildSegments,
  leafLabels,
  percent,
  type PageText,
  type Region,
  type Segment,
} from './segments'

function region(id: string, box: number[], over: Partial<Region> = {}): Region {
  return {
    id,
    kind: 'text_line',
    box,
    text: `text ${id}`,
    chosen: 0,
    content_class: 'cursive',
    line_score: 0.8,
    flagged: false,
    struck_out: false,
    read_by: ['trocr'],
    parent_id: null,
    row: null,
    col: null,
    readings: [],
    ...over,
  }
}

function page(number: number, regions: Region[]): PageText {
  return { number, text_read: true, needs_text: false, ocr_failures: [], regions }
}

function segment(id: string, position: number, regionIds: string[], label: string | null): Segment {
  return {
    id,
    slot_label: label,
    proposed_label: null,
    position,
    source: 'rule',
    flags: [],
    match_score: null,
    region_ids: regionIds,
    page_ids: [],
  }
}

describe('buildSegments', () => {
  const texts = new Map([
    [
      1,
      page(1, [
        region('a', [10, 10, 100, 30]),
        region('b', [10, 40, 120, 60], { line_score: 0.4, flagged: true }),
      ]),
    ],
    [2, page(2, [region('c', [5, 5, 50, 25], { line_score: null })])],
  ])

  it('orders segments as written, with their lines, pages and covered area', () => {
    const views = buildSegments(
      [segment('s2', 1, ['c'], null), segment('s1', 0, ['a', 'b', 'c'], '1')],
      texts,
    )
    expect(views.map((v) => v.segment.id)).toEqual(['s1', 's2'])
    const first = views[0]!
    expect(first.lines.map((l) => l.id)).toEqual(['a', 'b', 'c'])
    expect(first.pages).toEqual([1, 2])
    expect(first.boxes.get(1)).toEqual([10, 10, 120, 60])
    expect(first.boxes.get(2)).toEqual([5, 5, 50, 25])
    expect(first.lowLines).toBe(1)
    expect(views[1]!.label).toBeNull()
  })

  it('takes the confidence from the lines that have a score, and leaves out struck-out ones', () => {
    const [view] = buildSegments([segment('s', 0, ['a', 'b', 'c'], '1')], texts)
    expect(view!.confidence).toBeCloseTo(0.6)
    const struck = new Map([[1, page(1, [region('a', [0, 0, 5, 5], { struck_out: true })])]])
    expect(buildSegments([segment('s', 0, ['a'], '1')], struck)[0]!.confidence).toBeNull()
  })

  it('skips regions whose page has not been read', () => {
    const [view] = buildSegments([segment('s', 0, ['a', 'missing'], '1')], texts)
    expect(view!.lines.map((l) => l.id)).toEqual(['a'])
  })

  it('writes a missing percentage as a dash', () => {
    expect(percent(null)).toBe('—')
    expect(percent(0.876)).toBe('88%')
  })
})

describe('leafLabels', () => {
  it('lists questions, OR alternatives and sub-parts the way answers are labelled', () => {
    const document = {
      sections: [
        {
          items: [
            { type: 'question', label: '1', marks: 2, parts: [] },
            {
              type: 'or',
              alternatives: [
                { label: '12', parts: [{ label: 'a' }, { label: 'b' }] },
                { label: '13' },
              ],
            },
          ],
        },
      ],
    }
    expect(leafLabels(document)).toEqual(['1', '12.a', '12.b', '13'])
  })

  it('ignores a document it cannot read', () => {
    expect(leafLabels({})).toEqual([])
    expect(leafLabels({ sections: [{ items: [null, 3] }] })).toEqual([])
  })
})
