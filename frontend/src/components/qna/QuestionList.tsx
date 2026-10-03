import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { useDebounced } from '../../hooks/useDebounced'
import { Badge, EmptyState, GlassPanel, type Tone } from '../ui'
import { SmallButton } from '../schema/controls'

type Summary = components['schemas']['QuestionSummaryOut']
type Difficulty = components['schemas']['Difficulty']

const PAGE = 12
const TONE: Record<Difficulty, Tone> = { easy: 'emerald', medium: 'amber', hard: 'red' }
const filterBox =
  'rounded-xl border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs text-white focus:border-purple-500 focus:outline-none'

export function DifficultyPill({ level }: { level: Difficulty }) {
  return <Badge tone={TONE[level]}>{level[0]?.toUpperCase() + level.slice(1)}</Badge>
}

export function QuestionCard({ q, onOpen }: { q: Summary; onOpen: (id: string) => void }) {
  return (
    <GlassPanel
      as="article"
      aria-label={`Question ${q.code}`}
      className="flex flex-col gap-3 p-4 transition hover:border-purple-500/40"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded border border-purple-500/30 bg-purple-950 px-2 py-0.5 font-mono text-xs font-bold text-purple-300">
          {q.code}
        </span>
        <DifficultyPill level={q.difficulty} />
        <span className="rounded-full border border-purple-500/30 bg-purple-950/80 px-2.5 py-0.5 text-xs font-bold text-purple-300">
          {q.max_marks} {q.max_marks === 1 ? 'Mark' : 'Marks'}
        </span>
      </div>
      <p className="line-clamp-3 grow text-sm font-medium text-white">{q.text}</p>
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-400">
        <div className="flex gap-1">
          <dt className="sr-only">Subject</dt>
          <dd className="text-gray-200">{q.subject_name ?? 'No subject'}</dd>
        </div>
        {q.category && (
          <div className="flex gap-1">
            <dt className="sr-only">Topic</dt>
            <dd className="text-cyan-400">{q.category}</dd>
          </div>
        )}
        <div className="flex gap-1">
          <dt className="sr-only">Reference keys</dt>
          <dd>
            <i className="fa-solid fa-key mr-1 text-amber-400" aria-hidden="true" />
            {q.key_count} {q.key_count === 1 ? 'reference key' : 'reference keys'}
          </dd>
        </div>
      </dl>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] text-gray-400">
          {q.owned ? 'Your college' : `Owned by ${q.owner_name ?? 'another college'}`}
        </span>
        <button
          type="button"
          aria-label={`Open ${q.code}`}
          onClick={() => onOpen(q.id)}
          className="rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-xs text-gray-300 transition hover:bg-purple-900 hover:text-white"
        >
          Open <i className="fa-solid fa-chevron-right ml-1 text-[10px]" aria-hidden="true" />
        </button>
      </div>
    </GlassPanel>
  )
}

/** The repository browser: search and filters, then the question cards. */
export function QuestionList({
  onOpen,
  onNew,
}: {
  onOpen: (id: string) => void
  onNew: () => void
}) {
  const [keyword, setKeyword] = useState('')
  const [code, setCode] = useState('')
  const [subjectId, setSubjectId] = useState('')
  const [topic, setTopic] = useState('')
  const [difficulty, setDifficulty] = useState<Difficulty | ''>('')
  const [mine, setMine] = useState(false)
  const [page, setPage] = useState(0)
  const typed = useDebounced(`${keyword}\u0000${code}`, 300)
  const [debouncedKeyword = '', debouncedCode = ''] = typed.split('\u0000')

  const subjects = useQuery({
    queryKey: ['subjects'],
    queryFn: async () => (await api.GET('/api/v1/subjects')).data ?? [],
  })
  const topics = useQuery({
    queryKey: ['question-topics', subjectId],
    queryFn: async () =>
      (
        await api.GET('/api/v1/question-topics', {
          params: { query: subjectId ? { subject_id: subjectId } : {} },
        })
      ).data ?? [],
  })

  const questions = useQuery({
    queryKey: [
      'questions',
      debouncedKeyword,
      debouncedCode,
      subjectId,
      topic,
      difficulty,
      mine,
      page,
    ],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions', {
        params: {
          query: {
            keyword: debouncedKeyword,
            code: debouncedCode,
            subject_id: subjectId || undefined,
            topic,
            difficulty: difficulty || undefined,
            mine,
            limit: PAGE,
            offset: page * PAGE,
          },
        },
      })
      if (!data) throw new Error('questions unavailable')
      return data
    },
    placeholderData: (previous) => previous,
  })

  const reset = (apply: () => void) => {
    apply()
    setPage(0)
  }
  const total = questions.data?.total ?? 0
  const filtered = keyword || code || subjectId || topic || difficulty || mine

  return (
    <div className="space-y-4">
      <GlassPanel
        as="form"
        role="search"
        aria-label="Search questions"
        onSubmit={(e: { preventDefault: () => void }) => e.preventDefault()}
        className="flex flex-col gap-4 p-4"
      >
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs font-semibold text-gray-300">
            <i className="fa-solid fa-book text-sm text-purple-400" aria-hidden="true" />
            Subject:
            <select
              className={filterBox}
              value={subjectId}
              onChange={(e) =>
                reset(() => {
                  setSubjectId(e.target.value)
                  setTopic('')
                })
              }
            >
              <option value="">All Subjects</option>
              {subjects.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs font-semibold text-gray-300">
            <i className="fa-solid fa-tags text-sm text-cyan-400" aria-hidden="true" />
            Topic:
            <select
              className={filterBox}
              value={topic}
              onChange={(e) => reset(() => setTopic(e.target.value))}
            >
              <option value="">All Topics</option>
              {topics.data?.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs font-semibold text-gray-300">
            <i className="fa-solid fa-gauge text-sm text-amber-400" aria-hidden="true" />
            Difficulty:
            <select
              className={filterBox}
              value={difficulty}
              onChange={(e) => reset(() => setDifficulty(e.target.value as Difficulty | ''))}
            >
              <option value="">Any</option>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs font-semibold text-gray-300">
            <input
              type="checkbox"
              checked={mine}
              onChange={(e) => reset(() => setMine(e.target.checked))}
              className="h-4 w-4 accent-purple-500"
            />
            My college only
          </label>
        </div>
        <div className="flex flex-col gap-3 md:flex-row">
          <div className="relative grow">
            <i
              className="fa-solid fa-search absolute top-2.5 left-3 text-xs text-gray-400"
              aria-hidden="true"
            />
            <input
              type="search"
              aria-label="Search questions"
              placeholder="Search questions, answers and topics…"
              value={keyword}
              onChange={(e) => reset(() => setKeyword(e.target.value))}
              className={`${filterBox} w-full pl-9`}
            />
          </div>
          <input
            type="search"
            aria-label="Code"
            placeholder="Code, e.g. PHY-Q1"
            value={code}
            onChange={(e) => reset(() => setCode(e.target.value))}
            className={`${filterBox} md:w-48`}
          />
        </div>
      </GlassPanel>

      <div className="flex items-center justify-between px-1 text-xs text-gray-400">
        <span aria-live="polite">
          Showing <strong className="text-white">{questions.data?.items.length ?? 0}</strong> of{' '}
          <strong className="text-white">{total}</strong> Questions
        </span>
        {questions.isFetching && <span>Loading…</span>}
      </div>

      {questions.isError && (
        <p role="alert" className="text-xs text-red-400">
          Could not load the questions. Reload the page to try again.
        </p>
      )}

      {questions.data && questions.data.items.length === 0 && (
        <EmptyState
          icon="fa-solid fa-database"
          title={filtered ? 'No question matches' : 'The question bank is empty'}
          action={
            filtered ? undefined : (
              <SmallButton icon="fa-solid fa-plus" tone="purple" onClick={onNew}>
                Add the first question
              </SmallButton>
            )
          }
        >
          {filtered
            ? 'Change or clear the filters to see more.'
            : 'Add a question with its benchmark answer and rubric, or copy another college’s.'}
        </EmptyState>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {questions.data?.items.map((q) => (
          <QuestionCard key={q.id} q={q} onOpen={onOpen} />
        ))}
      </div>

      {total > PAGE && (
        <nav aria-label="Pages" className="flex items-center justify-center gap-3">
          <SmallButton tone="gray" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous
          </SmallButton>
          <span className="text-xs text-gray-400">
            Page {page + 1} of {Math.ceil(total / PAGE)}
          </span>
          <SmallButton
            tone="gray"
            disabled={(page + 1) * PAGE >= total}
            onClick={() => setPage(page + 1)}
          >
            Next
          </SmallButton>
        </nav>
      )}
    </div>
  )
}
