// src/routes/PaperGeneration.tsx — build a pattern into a paper filled from
// the shared, live question bank, with the paper's completeness front and
// center rather than buried in a warnings array nobody reads.
import { useState } from "react";

import { usePapers, type PaperGenerateResponse } from "../api/queries";
import { Empty } from "../components/Empty";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { SharedBankNotice } from "../components/SharedBankNotice";
import { PaperGenerateForm } from "../features/papers/PaperGenerateForm";
import { PaperPreview } from "../features/papers/PaperPreview";

export function PaperGeneration() {
  const [generated, setGenerated] = useState<PaperGenerateResponse | null>(null);

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Paper generation</h1>
      <p className="mt-1 text-sm text-slate-500">
        Fill a paper pattern with live questions from the shared bank, check it's actually complete, and preview it
        section by section before handing it out.
      </p>
      <div className="mt-3 no-print">
        <SharedBankNotice />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[380px_1fr]">
        <div className="no-print space-y-6">
          <PaperGenerateForm onGenerated={setGenerated} />
          <RecentPapers />
        </div>

        <div>
          {generated ? (
            <PaperPreview paper={generated} />
          ) : (
            <Empty
              title="No paper generated yet"
              description="Fill in a pattern id and a name on the left, then generate — the preview, completeness check, and print view show up here."
            />
          )}
        </div>
      </div>
    </div>
  );
}

function RecentPapers() {
  const papers = usePapers();

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-800">Recently generated papers</h2>
      <p className="mt-1 text-xs text-slate-400">
        Summary only — this list endpoint doesn't carry section detail, so a full preview is only available right
        after you generate a paper (left).
      </p>

      <div className="mt-3">
        {papers.isLoading && <Loading label="Loading papers…" />}
        {papers.error && <ErrorMessage message={(papers.error as Error).message} onRetry={() => void papers.refetch()} />}
        {papers.data && papers.data.items.length === 0 && (
          <Empty title="No papers generated yet" />
        )}
        {papers.data && papers.data.items.length > 0 && (
          <ul className="divide-y divide-slate-100 text-sm">
            {papers.data.items.map((p) => (
              <li key={p.paper_id} className="flex items-center justify-between gap-2 py-2">
                <div>
                  <p className="font-medium text-slate-800">{p.name}</p>
                  <p className="text-xs text-slate-400">{new Date(p.generated_at).toLocaleString()}</p>
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    p.status === "finalized" ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {p.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
