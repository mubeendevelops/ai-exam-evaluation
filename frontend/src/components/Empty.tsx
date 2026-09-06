// Shared "nothing here yet" state. Every list screen (uploads, question
// bank, papers, classes) uses this instead of rendering a blank table.
interface EmptyProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function Empty({ title, description, action }: EmptyProps) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-slate-300 px-6 py-14 text-center">
      <p className="text-base font-medium text-slate-700">{title}</p>
      {description && <p className="max-w-sm text-sm text-slate-500">{description}</p>}
      {action}
    </div>
  );
}
