"use client";

import { useRef, useState } from "react";
import { uploadCVs } from "@/lib/api";
import type { CV } from "@/lib/types";

interface Props {
  onUploaded: (cvs: CV[]) => void;
  onError: (message: string) => void;
}

const ACCEPT = ".pdf,.docx";

export default function CVUpload({ onUploaded, onError }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  async function handleFiles(files: FileList | File[]) {
    const arr = Array.from(files).filter(
      (f) => f.name.toLowerCase().endsWith(".pdf") || f.name.toLowerCase().endsWith(".docx"),
    );
    if (arr.length === 0) {
      onError("Only PDF or DOCX files are supported.");
      return;
    }
    setBusy(true);
    try {
      const created = await uploadCVs(arr);
      onUploaded(created);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files?.length) handleFiles(e.dataTransfer.files);
      }}
      className={`rounded-xl border-2 border-dashed p-8 text-center transition ${
        dragOver
          ? "border-brand-500 bg-brand-50"
          : "border-slate-300 bg-slate-50 hover:border-brand-400"
      }`}
    >
      <p className="text-sm text-slate-600">
        Drag & drop CVs here, or click to browse.
      </p>
      <p className="mt-1 text-xs text-slate-400">PDF or DOCX, up to 5 MB each</p>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => e.target.files && handleFiles(e.target.files)}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        className="mt-4 inline-flex items-center justify-center rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {busy ? "Uploading…" : "Select files"}
      </button>
    </div>
  );
}
