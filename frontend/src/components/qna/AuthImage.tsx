import { useQuery } from '@tanstack/react-query'
import { fetchBlob } from '../../api/files'

/** An image the API serves behind the bearer token, so it cannot be a plain `src`. */
export function AuthImage({ path, alt }: { path: string; alt: string }) {
  const image = useQuery({
    queryKey: ['file', path],
    queryFn: async () => URL.createObjectURL(await fetchBlob(path)),
    staleTime: Infinity,
    gcTime: 5 * 60_000,
  })
  if (!image.data) {
    return (
      <div className="flex h-24 w-32 items-center justify-center rounded-lg border border-gray-800 bg-gray-950 text-[10px] text-gray-500">
        {image.isError ? 'Unavailable' : 'Loading…'}
      </div>
    )
  }
  return (
    <img
      src={image.data}
      alt={alt}
      className="h-24 w-32 rounded-lg border border-gray-800 bg-white object-contain"
    />
  )
}
