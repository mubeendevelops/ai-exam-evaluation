// src/routes/Content.tsx — upload teacher content on the left, pick from it
// on the right. Finishes question generation's missing half: before this
// screen, POST /questions/generate needed a paragraph_id nothing in the app
// could produce, so a teacher had no way to reach it without a UUID typed
// by hand (see GenerateQuestionsForm.tsx's history).
import { useState } from "react";

import { ContentUploadForm } from "../features/content/ContentUploadForm";
import { ParagraphDetailPanel } from "../features/content/ParagraphDetailPanel";
import { ParagraphPicker } from "../features/content/ParagraphPicker";
import { SharedBankNotice } from "../components/SharedBankNotice";

export function Content() {
  const [selectedId, setSelectedId] = useState<string | undefined>();

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Content</h1>
      <p className="mt-1 text-sm text-slate-500">
        Upload text to generate questions from, then pick a paragraph below to send to question generation.
      </p>
      <div className="mt-3">
        <SharedBankNotice />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[380px_1fr]">
        <div>
          <ContentUploadForm />
        </div>

        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <ParagraphPicker selectedId={selectedId} onSelect={(p) => setSelectedId(p.paragraph_id)} />
          {selectedId && (
            <ParagraphDetailPanel paragraphId={selectedId} onClose={() => setSelectedId(undefined)} />
          )}
        </div>
      </div>
    </div>
  );
}
