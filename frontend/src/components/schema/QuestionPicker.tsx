import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { useDebounced } from '../../hooks/useDebounced'
import { QuestionModal } from '../qna/QuestionModal'
import { Modal } from '../ui'
import { SmallButton, compactInputClass } from './controls'
import { useDesignerLinks } from './kind'

type Found = components['schemas']['QuestionSummaryOut']

export const PICKER_PAGE = 10

/** Marks as typed in the designer ("2", "2.5") against a question's number. */
export function sameMarks(slot: string, question: number): boolean {
  const typed = Number(slot)
  return slot.trim() !== '' && Number.isFinite(typed) && Math.abs(typed - question) < 1e-9
}

function firstLine(text: string, max = 90): string {
  const line = text.split('\n')[0]?.trim() ?? ''
  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

/** One linked question, read by id (cached across slots). */
function useLinkedQuestion(id: string) {
  return useQuery({
    queryKey: ['question', id],
    enabled: id !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions/{question_id}', {
        params: { path: { question_id: id } },
      })
      if (!data) throw new Error('question unavailable')
      return data
    },
    retry: false,
    staleTime: 60_000,
  })
}

interface PickerProps {
  /** The linked question's id, or '' when not linked. */
  value: string
  /** The slot's (or sub-part's) marks: the question must carry the same. */
  marks: string
  onChange: (questionId: string) => void
  /** Accessible-name prefix, e.g. "Section 1 item 3" or "Section 1 item 3 part 1". */
  name: string
}

/**
 * The slot → question link of a paper (P34, closes O22/O26): the linked question's code, text
 * and marks, or a search of the paper's subject, or a new question made on the spot. The
 * server checks again that the subject and the marks agree.
 */
export function QuestionPicker({ value, marks, onChange, name }: PickerProps) {
  const { subjectId, locked } = useDesignerLinks()
  const [searching, setSearching] = useState(false)
  const [creating, setCreating] = useState(false)
  const linked = useLinkedQuestion(value)
  const question = linked.data
  const mismatch = question !== undefined && !sameMarks(marks, question.max_marks)

  return (
    <div className="space-y-1.5">
      {value === '' ? (
        <p className="text-[11px] text-amber-400">Not linked yet.</p>
      ) : linked.isError ? (
        <p className="text-[11px] text-red-300">
          The linked question was not found: it is not in your subjects, or it was deleted.
        </p>
      ) : question ? (
        <div className="rounded-lg border border-gray-800 bg-gray-900/70 px-2.5 py-1.5 text-[11px]">
          <span className="font-mono font-semibold text-cyan-300">{question.code}</span>{' '}
          <span className="text-gray-300">{firstLine(question.text)}</span>{' '}
          <span className={mismatch ? 'text-amber-400' : 'text-emerald-400'}>
            · {question.max_marks} marks
          </span>
          {mismatch && (
            <span className="block text-amber-400">
              The slot carries {marks || 0} marks: they must agree.
            </span>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-gray-400">Loading the linked question…</p>
      )}
      {!locked && (
        <div className="flex flex-wrap gap-2">
          <SmallButton
            icon="fa-solid fa-magnifying-glass"
            disabled={subjectId === ''}
            onClick={() => setSearching(true)}
            aria-label={`${value ? 'Change' : 'Link'} the question of ${name}`}
          >
            {value ? 'Change question' : 'Link question'}
          </SmallButton>
          {value && (
            <SmallButton
              icon="fa-solid fa-link-slash"
              tone="gray"
              onClick={() => onChange('')}
              aria-label={`Unlink the question of ${name}`}
            >
              Unlink
            </SmallButton>
          )}
        </div>
      )}
      {!locked && subjectId === '' && (
        <p className="text-[11px] text-gray-400">Pick the paper's subject first.</p>
      )}
      {searching && (
        <QuestionSearchDialog
          subjectId={subjectId}
          marks={marks}
          name={name}
          onClose={() => setSearching(false)}
          onPick={(id) => {
            onChange(id)
            setSearching(false)
          }}
          onNew={() => {
            setSearching(false)
            setCreating(true)
          }}
        />
      )}
      <QuestionModal
        open={creating}
        onClose={() => setCreating(false)}
        initial={{ subjectId, marks }}
        onSaved={(id) => {
          onChange(id)
          setCreating(false)
        }}
      />
    </div>
  )
}

/** The question search of a slot: the paper's subject, by code, text, topic or key. */
export function QuestionSearchDialog({
  subjectId,
  marks,
  name,
  onClose,
  onPick,
  onNew,
}: {
  subjectId: string
  marks: string
  name: string
  onClose: () => void
  onPick: (questionId: string) => void
  onNew: () => void
}) {
  const [text, setText] = useState('')
  const [offset, setOffset] = useState(0)
  const keyword = useDebounced(text.trim(), 300)
  const inputId = useId()
  const found = useQuery({
    queryKey: ['question-search', subjectId, keyword, offset],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions', {
        params: {
          query: {
            subject_id: subjectId,
            limit: PICKER_PAGE,
            offset,
            ...(keyword ? { keyword } : {}),
          },
        },
      })
      if (!data) throw new Error('search unavailable')
      return data
    },
    placeholderData: keepPreviousData,
  })
  const items = found.data?.items ?? []
  const total = found.data?.total ?? 0

  return (
    <Modal
      open
      onClose={onClose}
      size="max-w-3xl"
      eyebrow="Link a question"
      title={`Question for ${name}`}
      description={`Questions of the paper's subject. The slot carries ${marks || 0} marks; only a question with the same marks can be linked.`}
      footer={
        <div className="flex flex-wrap justify-between gap-2">
          <SmallButton icon="fa-solid fa-plus" onClick={onNew}>
            New question
          </SmallButton>
          <SmallButton tone="gray" onClick={onClose}>
            Cancel
          </SmallButton>
        </div>
      }
    >
      <div className="space-y-3">
        <label htmlFor={inputId} className="block text-xs font-medium text-gray-300">
          Search by code, text, topic or key
        </label>
        <input
          id={inputId}
          type="search"
          className={compactInputClass}
          value={text}
          maxLength={200}
          autoFocus
          onChange={(e) => {
            setText(e.target.value)
            setOffset(0)
          }}
        />
        {found.isError && (
          <p role="alert" className="text-xs text-red-300">
            The question bank could not be searched.
          </p>
        )}
        {found.data && items.length === 0 && (
          <p className="text-xs text-gray-400">
            No question matches. Use New question to write one for this slot.
          </p>
        )}
        {items.length > 0 && (
          <ul aria-label="Matching questions" className="space-y-2">
            {items.map((q) => (
              <SearchRow key={q.id} question={q} marks={marks} onPick={() => onPick(q.id)} />
            ))}
          </ul>
        )}
        {total > PICKER_PAGE && (
          <div className="flex items-center justify-between text-xs text-gray-400">
            <span aria-live="polite">
              Showing {offset + 1}–{Math.min(offset + PICKER_PAGE, total)} of {total}
            </span>
            <div className="flex gap-2">
              <SmallButton
                tone="gray"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PICKER_PAGE))}
              >
                Previous
              </SmallButton>
              <SmallButton
                tone="gray"
                disabled={offset + PICKER_PAGE >= total}
                onClick={() => setOffset(offset + PICKER_PAGE)}
              >
                Next
              </SmallButton>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

function SearchRow({
  question,
  marks,
  onPick,
}: {
  question: Found
  marks: string
  onPick: () => void
}) {
  const fits = sameMarks(marks, question.max_marks)
  return (
    <li className="flex items-start justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/60 p-2.5">
      <div className="min-w-0 text-xs">
        <p>
          <span className="font-mono font-semibold text-cyan-300">{question.code}</span>{' '}
          <span className={fits ? 'text-emerald-400' : 'text-amber-400'}>
            · {question.max_marks} marks
          </span>
        </p>
        <p className="line-clamp-2 text-gray-300">{question.text}</p>
        {!fits && (
          <p className="text-[11px] text-amber-400">
            {question.max_marks} marks: this slot carries {marks || 0}.
          </p>
        )}
      </div>
      <SmallButton
        icon="fa-solid fa-link"
        disabled={!fits}
        onClick={onPick}
        aria-label={`Link ${question.code}`}
      >
        Link
      </SmallButton>
    </li>
  )
}
