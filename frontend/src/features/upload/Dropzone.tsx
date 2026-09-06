// src/features/upload/Dropzone.tsx — drag-and-drop PDF picker.
//
// This is a CONVENIENCE, not the validation: the server checks the PDF
// signature on the bytes it actually receives (api/routers/upload.py,
// PDF_MAGIC), because a filename/extension is client-controlled and trivially
// wrong. The accept filter here just saves a round trip for the common
// mistake (dropping a .docx or a photo) — a renamed file still reaches the
// server and gets a real 415 with a plain-language reason.
import { useCallback } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";

interface DropzoneProps {
  onFileSelected: (file: File) => void;
  disabled?: boolean;
}

export function Dropzone({ onFileSelected, disabled }: DropzoneProps) {
  const onDrop = useCallback(
    (accepted: File[], rejected: FileRejection[]) => {
      if (accepted[0]) {
        onFileSelected(accepted[0]);
      } else if (rejected[0]) {
        // A rejection here is purely the accept-filter shortcut above; the
        // file itself is not touched, so nothing needs cleaning up.
        onFileSelected(rejected[0].file);
      }
    },
    [onFileSelected],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled,
    multiple: false,
    accept: { "application/pdf": [".pdf"] },
  });

  return (
    <div
      {...getRootProps()}
      className={[
        "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-14 text-center transition-colors",
        disabled ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400" : "",
        !disabled && isDragActive ? "border-slate-500 bg-slate-100" : "",
        !disabled && !isDragActive ? "border-slate-300 hover:border-slate-400 hover:bg-slate-50" : "",
      ].join(" ")}
    >
      <input {...getInputProps()} aria-label="Upload a scanned answer booklet PDF" />
      <p className="text-base font-medium text-slate-700">
        {isDragActive ? "Drop the booklet here" : "Drag and drop a scanned answer booklet"}
      </p>
      <p className="max-w-sm text-sm text-slate-500">
        PDF only, one booklet at a time. Or click to browse your files.
      </p>
    </div>
  );
}
