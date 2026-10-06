import type { components } from '../api/schema'
import { COLLEGE_ID } from './fixtures'

export type Summary = components['schemas']['QuestionSummaryOut']
export type Detail = components['schemas']['QuestionOut']

export const SUBJECT_ID = '33333333-3333-4333-8333-333333333333'
export const OTHER_COLLEGE = '99999999-9999-4999-8999-999999999999'

export function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    id: 'q-1',
    version: 1,
    code: 'PHY-Q1',
    text: 'State Lenz’s law.',
    max_marks: 4,
    difficulty: 'easy',
    category: 'Electromagnetism',
    subject_id: SUBJECT_ID,
    subject_name: 'Physics',
    key_count: 2,
    owning_college_id: COLLEGE_ID,
    owner_name: 'Synthetic College',
    owned: true,
    copied_from: null,
    ...overrides,
  }
}

export function detail(overrides: Partial<Detail> = {}): Detail {
  return {
    ...summary(),
    reference_answers: [
      {
        id: 'a-1',
        version: 1,
        text: 'The induced emf opposes the change of flux.',
        guidance_only: false,
        synthetic: false,
      },
    ],
    rubric: {
      criteria: [
        {
          version: 1,
          criterion: {
            id: 'c-1',
            type: 'list',
            label: 'Names the effect',
            weight: 2,
            params: { items: [{ term: 'opposes', synonyms: ['resists'] }], required_count: 1 },
          },
        },
        {
          version: 1,
          criterion: {
            id: 'c-2',
            type: 'semantic',
            label: 'Explains why',
            weight: 2,
            params: { reference_statement: 'The emf opposes the change that causes it.' },
          },
        },
      ],
      total: 4,
      max_marks: 4,
      complete: true,
    },
    glossary: {
      teacher_terms: ['induced emf'],
      reference_labels: ['coil'],
      terms: ['induced emf', 'coil'],
    },
    key_files: [
      {
        id: 'f-1',
        name: 'key.pdf',
        media_type: 'application/pdf',
        size_bytes: 2048,
        keywords: ['lenz'],
        content_url: '/api/v1/questions/q-1/key-files/f-1/content',
      },
    ],
    diagrams: [
      {
        id: 'd-1',
        name: 'coil.png',
        node_count: 0,
        edge_count: 0,
        labels: [],
        content_url: '/api/v1/questions/q-1/diagrams/d-1/content',
        kind: 'flowchart',
        recognition: 'pending',
        version: 1,
        graph_url: '/api/v1/questions/q-1/diagrams/d-1/graph',
      },
    ],
    ...overrides,
  }
}
