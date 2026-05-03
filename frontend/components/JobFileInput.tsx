"use client";

import { useRef, useState } from "react";
import { parseJobFromFile } from "@/lib/api";
import type { JobParsed } from "@/lib/types";

interface Props {
  /** Called with the extracted text + parsed JD when extraction succeeds. */
  onExtracted: (extractedText: string, parsed: JobParsed) => void;
  onError: (message: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

const ACCEPT = ".pdf,.docx,.txt";

export default function JobFileInput({
  onExtracted,
  onError,
  disabled,
  disabledReason,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ filename: string; parsed: JobParsed } | null>(null);

  async function handleFile(file: File) {
    setBusy(true);
    setPreview(null);
    try {
      const result = await parseJobFromFile(file);
      if (!result.success || !result.parsed_job) {
        onError(
          result.error ||
            `Could not extract a JD from ${file.name}. Try a different file or paste manually.`,
        );
        return;
      }
      setPreview({ filename: result.filename, parsed: result.parsed_job });
      onExtracted(result.extracted_text, result.parsed_job);
    } catch (err) {
      onError(err instanceof Error ? err.message : "File upload failed.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy || disabled}
          className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Reading…" : "Upload JD file"}
        </button>
        <p className="text-xs text-slate-500">
          {disabled && disabledReason
            ? disabledReason
            : "Accepts PDF, DOCX, or TXT (≤ 5 MB). Scanned PDFs without text won't extract — paste the JD instead."}
        </p>
      </div>

      {preview && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
            Extracted from {preview.filename}
          </p>
          <p className="mt-1 font-semibold text-slate-900">
            {preview.parsed.job_title || "Untitled role"}
          </p>
          <p className="text-xs text-slate-600">
            {[preview.parsed.company, preview.parsed.location].filter(Boolean).join(" · ") ||
              "No company / location detected"}
          </p>
          {preview.parsed.required_skills.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {preview.parsed.required_skills.slice(0, 8).map((s) => (
                <span
                  key={s}
                  className="rounded-full bg-white px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200"
                >
                  {s}
                </span>
              ))}
              {preview.parsed.required_skills.length > 8 && (
                <span className="text-xs text-slate-500">
                  +{preview.parsed.required_skills.length - 8} more
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
