import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api } from '../../api/client'
import { uploadBooklet } from '../../api/booklets'
import { SelectInput, SmallButton } from '../schema/controls'
import { EmptyState, GlassPanel, useToast } from '../ui'
import { checkFiles, Dropzone, inPageOrder } from './Dropzone'
import { STATUS } from './model'
import { StudentPicker, type Student } from './StudentPicker'
import { SubmissionCard } from './SubmissionCard'

interface Item {
  key: number
  student: Student
  blueprintId: string
  exam: string
  files: File[]
  state: 'sending' | 'duplicate' | 'failed'
  message?: string
}

/** Step 1, "Scan Upload": pick the student and the exam, drop the scans, watch the queue. */
export function UploadStep({ onOpen }: { onOpen: (bookletId: string) => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [student, setStudent] = useState<Student | null>(null)
  const [blueprintId, setBlueprintId] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [items, setItems] = useState<Item[]>([])
  const next = useRef(1)

  const exams = useQuery({
    queryKey: ['blueprints'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/blueprints')
      if (!data) throw new Error('exams unavailable')
      return data
    },
  })
  const booklets = useQuery({
    queryKey: ['booklets'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets', { params: { query: { limit: 100 } } })
      if (!data) throw new Error('booklets unavailable')
      return data
    },
    // The machine works on its own: keep the grid current while any booklet is in its hands.
    refetchInterval: (query) =>
      query.state.data?.items.some((b) => STATUS[b.status].working) ? 4000 : false,
  })

  const list = booklets.data
  const sending = items.filter((i) => i.state === 'sending').length
  const free = list ? list.max_waiting - list.waiting - sending : 0
  const exam = exams.data?.find((e) => e.id === blueprintId)

  const patch = (key: number, change: Partial<Item>) =>
    setItems((all) => all.map((i) => (i.key === key ? { ...i, ...change } : i)))
  const drop = (key: number) => setItems((all) => all.filter((i) => i.key !== key))

  async function send(item: Item, allowDuplicate: boolean) {
    patch(item.key, { state: 'sending', message: undefined })
    const result = await uploadBooklet(
      item.student.id,
      item.blueprintId,
      item.files,
      allowDuplicate,
    )
    if (result.ok) {
      drop(item.key)
      toast.show(
        `${item.student.name}: ${item.files.length === 1 ? 'file' : `${item.files.length} pages`} uploaded. The pages are cleaned next.`,
      )
    } else if (result.kind === 'duplicate') {
      patch(item.key, { state: 'duplicate', message: result.message })
    } else {
      patch(item.key, { state: 'failed', message: result.message })
    }
    await queryClient.invalidateQueries({ queryKey: ['booklets'] })
  }

  function accept(dropped: File[]) {
    setProblem(null)
    if (!student || !exam) return
    const why = checkFiles(dropped)
    if (why) {
      setProblem(why)
      return
    }
    const item: Item = {
      key: next.current++,
      student,
      blueprintId: exam.id,
      exam: exam.title,
      files: inPageOrder(dropped),
      state: 'sending',
    }
    setItems((all) => [...all, item])
    void send(item, false)
  }

  const hint = !student
    ? 'Choose the student first.'
    : !exam
      ? 'Choose the exam first.'
      : free <= 0 && list
        ? `You already have ${list.max_waiting} booklets waiting. They are processed one at a time.`
        : undefined
  const disabled = hint !== undefined

  return (
    <div className="space-y-6">
      <GlassPanel as="section" aria-label="Who and what" className="grid gap-4 p-4 md:grid-cols-2">
        <StudentPicker value={student} onChange={setStudent} />
        <SelectInput
          label="Exam"
          value={blueprintId}
          onChange={(e) => setBlueprintId(e.target.value)}
          hint={
            exams.isError
              ? 'The exams could not be loaded.'
              : exam
                ? `${exam.course_code} · ${exam.total_marks} marks · ${exam.question_count} questions`
                : 'The exam blueprint the booklet is marked against.'
          }
        >
          <option value="">Choose an exam…</option>
          {exams.data?.map((e) => (
            <option key={e.id} value={e.id} disabled={e.unlinked_count > 0}>
              {e.title}
              {e.unlinked_count > 0 ? ` (${e.unlinked_count} questions not linked)` : ''}
            </option>
          ))}
        </SelectInput>
      </GlassPanel>

      <Dropzone disabled={disabled} hint={hint} onFiles={accept} />
      {problem && (
        <p role="alert" className="text-xs text-red-300">
          {problem}
        </p>
      )}

      {(items.length > 0 || (list && list.waiting > 0)) && (
        <section aria-label="Upload queue" className="space-y-2">
          <h3 className="text-sm font-semibold tracking-wider text-gray-400 uppercase">
            <i className="fa-solid fa-list-check mr-2 text-cyan-400" aria-hidden="true" />
            Queue ({list ? `${list.waiting + sending} of ${list.max_waiting}` : sending})
          </h3>
          <ul className="space-y-2">
            {items.map((i) => (
              <li
                key={i.key}
                className="glass-panel flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-800 p-3 text-xs"
              >
                <span className="text-gray-200">
                  <strong className="text-white">{i.student.name}</strong>{' '}
                  <span className="font-mono text-gray-400">{i.student.usn}</span> · {i.exam} ·{' '}
                  {i.files.length === 1 ? i.files[0]?.name : `${i.files.length} page images`}
                </span>
                {i.state === 'sending' && <span className="text-cyan-300">Uploading…</span>}
                {i.state !== 'sending' && (
                  <span className="flex flex-wrap items-center gap-2">
                    <span
                      role="alert"
                      className={i.state === 'duplicate' ? 'text-amber-300' : 'text-red-300'}
                    >
                      {i.message}
                    </span>
                    {i.state === 'duplicate' && (
                      <SmallButton
                        icon="fa-solid fa-upload"
                        tone="purple"
                        onClick={() => void send(i, true)}
                      >
                        Upload anyway
                      </SmallButton>
                    )}
                    {i.state === 'failed' && (
                      <SmallButton icon="fa-solid fa-rotate" onClick={() => void send(i, false)}>
                        Try again
                      </SmallButton>
                    )}
                    <SmallButton tone="gray" onClick={() => drop(i.key)}>
                      Dismiss
                    </SmallButton>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Uploaded student answer submissions">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold tracking-wider text-gray-400 uppercase">
          <i className="fa-solid fa-folder-open text-purple-400" aria-hidden="true" />
          Uploaded Student Answer Submissions ({list?.total ?? 0})
        </h3>
        {booklets.isError && (
          <p role="alert" className="text-xs text-red-300">
            The submissions could not be loaded.
          </p>
        )}
        {list && list.items.length === 0 && (
          <EmptyState icon="fa-solid fa-file-circle-plus" title="No submissions yet">
            Choose a student and an exam, then drop the scanned booklet above.
          </EmptyState>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list?.items.map((b) => (
            <SubmissionCard key={b.id} booklet={b} onOpen={onOpen} />
          ))}
        </div>
      </section>
    </div>
  )
}
