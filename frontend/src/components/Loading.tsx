// Shared loading indicator. Used by every screen instead of a bespoke
// spinner per page, so "the app is thinking" always looks the same.
interface LoadingProps {
  label?: string;
}

export function Loading({ label = "Loading…" }: LoadingProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-500" role="status">
      <span
        aria-hidden="true"
        className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600"
      />
      <p>{label}</p>
    </div>
  );
}
