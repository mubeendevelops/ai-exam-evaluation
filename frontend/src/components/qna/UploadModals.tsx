import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { uploadFile } from '../../api/files'
import { FormError } from '../public/fields'
import { SmallButton, TextInput } from '../schema/controls'
import { Modal } from '../ui'

/** The rule of requirements C9, shown in both upload dialogs. */
function StudentDataRule({
  confirmed,
  onChange,
}: {
  confirmed: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <div className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-950/30 p-3">
      <p className="text-xs text-amber-100">
        <i className="fa-solid fa-user-shield mr-1.5 text-amber-300" aria-hidden="true" />
        <strong>Keys hold no student data.</strong> Answer keys and reference diagrams are written
        by faculty. Never upload a student's submission, or anything with a student's name, USN or
        handwriting.
      </p>
      <label className="flex items-start gap-2 text-xs font-semibold text-white">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-purple-500"
        />
        I confirm this file contains no student data.
      </label>
    </div>
  )
}

interface Common {
  open: boolean
  onClose: () => void
  questionId: string
  questionCode: string
}

function UploadForm({
  questionId,
  path,
  accept,
  kind,
  withKeywords,
  fallback,
  done,
  onClose,
}: {
  questionId: string
  path: string
  accept: string
  kind: string
  withKeywords: boolean
  fallback: string
  done: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [keywords, setKeywords] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    setProblem(null)
    const result = await uploadFile(
      path,
      file,
      {
        filename: file.name,
        confirm_no_student_data: confirmed,
        ...(withKeywords
          ? {
              keywords: keywords
                .split(',')
                .map((k) => k.trim())
                .filter((k) => k !== ''),
            }
          : {}),
      },
      fallback,
    )
    setBusy(false)
    if (!result.ok) {
      setProblem(result.message)
      return
    }
    await queryClient.invalidateQueries({ queryKey: ['question', questionId] })
    await queryClient.invalidateQueries({ queryKey: ['questions'] })
    setSaved(true)
  }

  if (saved) {
    return (
      <div className="space-y-4">
        <p role="status" className="text-sm text-emerald-300">
          {done}
        </p>
        <div className="flex justify-end">
          <SmallButton tone="gray" onClick={onClose}>
            Close
          </SmallButton>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label={`Upload ${kind}`}>
      <div>
        <label htmlFor={`file-${kind}`} className="mb-1 block text-xs font-semibold text-gray-300">
          {kind[0]?.toUpperCase() + kind.slice(1)} file
        </label>
        <input
          id={`file-${kind}`}
          type="file"
          accept={accept}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full rounded-xl border border-dashed border-gray-700 bg-gray-900/60 p-3 text-xs text-gray-200 file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-700 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white"
        />
      </div>
      {withKeywords && (
        <TextInput
          label="Keywords (optional, comma separated)"
          placeholder="focal length, convex lens"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
        />
      )}
      <StudentDataRule confirmed={confirmed} onChange={setConfirmed} />
      <FormError message={problem} />
      <div className="flex justify-end gap-2">
        <SmallButton tone="gray" onClick={onClose}>
          Cancel
        </SmallButton>
        <button
          type="submit"
          disabled={busy || !file || !confirmed}
          className="rounded-xl bg-cyan-600 px-5 py-2 text-xs font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Uploading…' : `Upload ${kind}`}
        </button>
      </div>
    </form>
  )
}

/** "Upload Answer Key": a PDF or image with optional keywords. */
export function UploadKeyModal({ open, onClose, questionId, questionCode }: Common) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={questionCode}
      title="Upload Answer Key"
      description="A PDF, PNG or JPEG key or sample answer, up to 10 MB."
    >
      <UploadForm
        questionId={questionId}
        path={`/api/v1/questions/${questionId}/key-files`}
        accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
        kind="answer key"
        withKeywords
        fallback="Could not upload the file."
        done="The answer key was attached."
        onClose={onClose}
      />
    </Modal>
  )
}

/** "Upload reference diagram": a PNG; its nodes and edges are read from it later. */
export function UploadDiagramModal({ open, onClose, questionId, questionCode }: Common) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={questionCode}
      title="Upload reference diagram"
      description="A PNG, up to 5 MB. Student drawings are compared with it."
    >
      <UploadForm
        questionId={questionId}
        path={`/api/v1/questions/${questionId}/diagrams`}
        accept=".png,image/png"
        kind="reference diagram"
        withKeywords={false}
        fallback="Could not upload the diagram."
        done="The reference diagram was attached. Its nodes and edges are read from it by the diagram recognizer."
        onClose={onClose}
      />
    </Modal>
  )
}
