import { describe, expect, it } from 'vitest'
import { questionEcho } from './questionEcho'
import type { LineView } from './segments'

const lines = (...texts: string[]): LineView[] =>
  texts.map((text, k) => ({
    id: `l${k}`,
    page: 1,
    box: [0, k * 10, 100, k * 10 + 9],
    kind: 'text_line',
    text,
    flagged: false,
    struck: false,
    score: 0.9,
    readBy: [],
  }))

const Q1 = 'Ethical dilemma: explain what an ethical dilemma is and discuss it with examples.'

describe('questionEcho', () => {
  it('finds a copied question followed by an answer mark the OCR misread', () => {
    const text = lines(
      '1 . Discuss the ethical dilemma with examples .',
      'Ano- A dilemma arises when two duties pull',
      'apart , for example loyalty and honesty',
    )
    expect(questionEcho(text, Q1)).toBe(1)
  })

  it('takes everything before an answer mark when the student words the question their way', () => {
    const text = lines(
      '2 . Write short notes on the cultural',
      'influence over',
      'firms .',
      'Avis- Each country brings its own values',
      'into the way it does trade',
    )
    expect(
      questionEcho(text, 'Influence of cultural ethos on business: India, Korea, Japan and China.'),
    ).toBe(3)
  })

  it('needs a couple of question words before the answer mark', () => {
    const text = lines('Name : roll no 12', 'Ans:- A dilemma is a hard choice', 'between duties')
    expect(questionEcho(text, Q1)).toBe(0)
  })

  it('keeps a one-word line that ends the copied question', () => {
    const text = lines(
      '6 . Two constitutional provisions',
      'that make India a secular',
      'state',
      'freedom of religion for every citizen',
    )
    expect(
      questionEcho(text, 'Mention 2 constitutional provisions that make India a secular state.'),
    ).toBe(3)
  })

  it('finds a copy over two lines without an answer mark when it covers the question', () => {
    const text = lines(
      'Q2) Influence of cultural ethos on business :',
      'India , Korea , Japan and Chlna',
      'Indian ethos is rooted in the Vedas and values family',
    )
    expect(
      questionEcho(text, 'Influence of cultural ethos on business: India, Korea, Japan and China.'),
    ).toBe(2)
  })

  it('leaves an answer that only opens by rephrasing part of the question', () => {
    const text = lines(
      'An ethical dilemma is a choice between two duties',
      'such as honesty and loyalty in a company',
    )
    expect(questionEcho(text, Q1)).toBe(0)
  })

  it('finds nothing when the answer starts straight away', () => {
    expect(questionEcho(lines('Ans:- Startups get seed funds', 'from the government'), Q1)).toBe(0)
    expect(questionEcho(lines('Values guide the leader in hard', 'times at work'), Q1)).toBe(0)
  })

  it('never folds the whole segment away', () => {
    expect(questionEcho(lines('Explain the ethical dilemma with examples'), Q1)).toBe(0)
  })

  it('stops at a struck-out line or a drawing', () => {
    const struck = lines('explain the ethical dilemma with examples', 'Ans: a choice')
    struck[0] = { ...struck[0]!, struck: true }
    expect(questionEcho(struck, Q1)).toBe(0)
    const drawing = lines('', 'explain the ethical dilemma with examples', 'Ans: a choice')
    drawing[0] = { ...drawing[0]!, kind: 'diagram' }
    expect(questionEcho(drawing, Q1)).toBe(0)
  })
})
