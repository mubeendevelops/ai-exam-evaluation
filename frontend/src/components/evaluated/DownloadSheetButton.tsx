import { useMutation } from '@tanstack/react-query'
import { fetchBlob, saveBlob } from '../../api/files'
import { SmallButton } from '../schema/controls'
import { useToast } from '../ui'

/**
 * Saves one result sheet PDF. The file is served behind the bearer token, so it is fetched with
 * the token and saved from a blob; a plain link would arrive without it.
 */
export function DownloadSheetButton({
  url,
  filename,
  label,
  tone = 'cyan',
}: {
  url: string | null | undefined
  filename: string
  label: string
  tone?: 'cyan' | 'purple' | 'gray'
}) {
  const toast = useToast()
  const download = useMutation({
    mutationFn: async () => saveBlob(await fetchBlob(url as string), filename),
    onError: (e) =>
      toast.show(
        e instanceof Error && e.message.includes('404')
          ? 'This result sheet has no stored PDF.'
          : 'The PDF could not be downloaded. Try again.',
        'red',
      ),
  })
  if (!url) return null
  return (
    <SmallButton
      tone={tone}
      icon="fa-solid fa-file-pdf"
      disabled={download.isPending}
      onClick={() => download.mutate()}
    >
      {label}
    </SmallButton>
  )
}

export function sheetFileName(usn: string | undefined, version: number): string {
  const safe = (usn ?? '').replace(/[^A-Za-z0-9_-]/g, '')
  return `result-sheet-${safe || 'student'}-v${version}.pdf`
}
