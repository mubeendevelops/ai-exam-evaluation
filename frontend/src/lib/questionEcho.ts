import { isTextLine, type LineView } from './segments'

/*
 * Students often copy the question out before answering it ("1. Illustrate the ethical dilemma
 * with examples." then "Ans:- …"). The screens show the official question instead and fold the
 * student's copy away. This finds that copy: the leading lines of a segment whose words are
 * the question's words, read loosely because the OCR misreads about one character in four.
 * Display only (D158): scoring still reads every line.
 */

/** The most leading lines a copied question is looked for in. */
const MAX_LINES = 6
/** Share of a line's words that must be the question's words. */
const LINE_MATCH = 0.6
/** Without an answer mark after it, the copy must cover this share of the question's words. */
const COVERAGE = 0.5

const STOP = new Set(
  (
    'a an and are as at be but by for from has have how in is it its of on or that the their ' +
    'them then there these this those to was were what when where which who why will with your ' +
    'you any all can do does each into about also its q ques question qn no marks mark'
  ).split(' '),
)

/** "1.", "Q1)", "12 b)", "(ii)" at the start of a line. */
const LABEL =
  /^\s*(?:q(?:ues(?:tion)?|n)?\.?\s*(?:no\.?)?\s*)?\(?\d{1,2}\s*\)?\s*(?:\(?[a-h]\)?)?\s*[.):-]?\s*|^\s*\((?:i|ii|iii|iv|v|vi)\)\s*/i

/** "Ans:-", "Answer:", and the OCR's usual misreadings of "Ans" ("Ano-", "An5:", "Avis-"): a
 * short word from a to s or o, then a dash or colon. */
const ANSWER_MARK = /^\s*(?:ans(?:wer)?\s*[:;.=>\-–—]|a[a-z0-9]{1,2}[so5]\s*[:;\-–—=>])/i
/** Before an answer mark, the lines need only this many of the question's words. */
const MARKED_WORDS = 2

function words(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length >= 2 && !STOP.has(w))
}

function distance(a: string, b: string, limit: number): number {
  if (Math.abs(a.length - b.length) > limit) return limit + 1
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    const row = [i]
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(
        (prev[j] ?? 0) + 1,
        (row[j - 1] ?? 0) + 1,
        (prev[j - 1] ?? 0) + (a[i - 1] === b[j - 1] ? 0 : 1),
      )
    }
    prev = row
  }
  return prev[b.length] ?? limit + 1
}

/** One word read by the OCR against one word of the question. */
function same(read: string, word: string): boolean {
  if (read === word) return true
  const short = Math.min(read.length, word.length)
  // "dilemma" / "dilemmas", "explain" / "explained"
  if (short >= 5 && (read.startsWith(word.slice(0, 5)) || word.startsWith(read.slice(0, 5)))) {
    return true
  }
  const limit = short >= 8 ? 2 : short >= 4 ? 1 : 0
  return limit > 0 && distance(read, word, limit) <= limit
}

/** Lines read as text, up to the first struck-out line or drawing. */
function leading(lines: readonly LineView[]): string[] {
  const out: string[] = []
  for (const line of lines.slice(0, MAX_LINES + 1)) {
    if (!isTextLine(line) || line.struck) break
    out.push(line.text.replace(LABEL, ''))
  }
  return out
}

function matches(text: string, asked: readonly string[]): { read: number; hits: string[] } {
  const read = words(text)
  const hits = read.flatMap((w) => asked.filter((q) => same(w, q)).slice(0, 1))
  return { read: read.length, hits }
}

/**
 * How many leading lines of a segment are the student's copy of the question (0: none found).
 * With an answer mark ("Ans:-") in the first lines, everything before it is the copy when it
 * holds a couple of the question's words: students often word the question their own way.
 * Without one, each line must be mostly the question's words and together cover half of it.
 * Never the whole segment: at least one line is left as the answer.
 */
export function questionEcho(lines: readonly LineView[], question: string): number {
  const asked = [...new Set(words(question))]
  if (asked.length === 0) return 0
  const texts = leading(lines)

  const mark = texts.findIndex((t) => ANSWER_MARK.test(t))
  if (mark > 0) {
    const found = new Set(texts.slice(0, mark).flatMap((t) => matches(t, asked).hits))
    if (found.size >= Math.min(MARKED_WORDS, asked.length)) return mark
  }

  const found = new Set<string>()
  let count = 0
  for (const text of texts.slice(0, MAX_LINES)) {
    if (ANSWER_MARK.test(text)) break
    const { read, hits } = matches(text, asked)
    if (read === 0) {
      // a lone label: part of the copy only when more of it follows
      count++
      continue
    }
    if (hits.length === 0 || hits.length / read < LINE_MATCH) break
    for (const h of hits) found.add(h)
    count++
  }
  // trailing lone labels belong to the answer
  while (count > 0 && words(texts[count - 1] ?? '').length === 0) count--
  if (count === 0 || count >= lines.length) return 0
  return found.size / asked.length >= COVERAGE ? count : 0
}
