"use client";

import { useEffect, useState } from "react";
import {
  addUrlSource,
  deleteCV,
  deleteUrlSource,
  listAllSources,
  refreshUrlSource,
  uploadCVs,
  uploadMarkdownCV,
  API_BASE,
} from "@/lib/api";
import type { UnifiedSource } from "@/lib/types";

interface Props {
  onError: (msg: string) => void;
  /** Bumped whenever sources change so downstream sections (matching,
   *  tailored CV) can refetch the now-rebuilt master library. */
  onSourcesChanged?: () => void;
}

const KIND_LABEL: Record<UnifiedSource["kind"], string> = {
  cv: "CV",
  document: "Doc",
  web: "Web",
  github_user: "GitHub user",
  github_repo: "GitHub repo",
};

const KIND_COLOR: Record<UnifiedSource["kind"], string> = {
  cv: "bg-brand-100 text-brand-800 ring-brand-200",
  document: "bg-amber-100 text-amber-800 ring-amber-200",
  web: "bg-sky-100 text-sky-800 ring-sky-200",
  github_user: "bg-slate-200 text-slate-800 ring-slate-300",
  github_repo: "bg-slate-200 text-slate-800 ring-slate-300",
};

export default function UnifiedSourcePanel({ onError, onSourcesChanged }: Props) {
  const [sources, setSources] = useState<UnifiedSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [addingUrl, setAddingUrl] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [uploadingMd, setUploadingMd] = useState(false);
  const [uploadingDoc, setUploadingDoc] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      setSources(await listAllSources());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Load failed.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function notifyChanged() {
    refresh();
    onSourcesChanged?.();
  }

  async function handleUploadCVs(files: File[]) {
    if (!files.length) return;
    setUploadingFiles(true);
    try {
      await uploadCVs(files);
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploadingFiles(false);
    }
  }

  async function handleUploadMarkdown(file: File) {
    setUploadingMd(true);
    try {
      await uploadMarkdownCV(file);
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Markdown upload failed.");
    } finally {
      setUploadingMd(false);
    }
  }

  async function handleUploadDocument(files: File[]) {
    if (!files.length) return;
    setUploadingDoc(true);
    try {
      // Reuse the existing /api/profile/build endpoint — it persists
      // Documents and triggers a library rebuild as a side effect.
      const formData = new FormData();
      for (const f of files) formData.append("files", f);
      const res = await fetch(`${API_BASE}/api/profile/build`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `HTTP ${res.status}`);
      }
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Document upload failed.");
    } finally {
      setUploadingDoc(false);
    }
  }

  async function handleAddUrl() {
    const url = urlInput.trim();
    if (!url) return;
    if (!/^https?:\/\//.test(url)) {
      onError("URL must start with http:// or https://");
      return;
    }
    setAddingUrl(true);
    try {
      await addUrlSource(url);
      setUrlInput("");
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "URL ingest failed.");
    } finally {
      setAddingUrl(false);
    }
  }

  async function handleDelete(s: UnifiedSource) {
    if (!confirm(`Remove "${s.title}"? Master CV will rebuild without it.`)) return;
    try {
      if (s.kind === "cv") {
        await deleteCV(s.id);
        // Cv delete doesn't auto-rebuild; rebuild via library endpoint.
        await fetch(`${API_BASE}/api/cv/library/rebuild`, { method: "POST" });
      } else if (s.kind === "web" || s.kind === "github_user" || s.kind === "github_repo") {
        await deleteUrlSource(s.id);
      } else {
        // Documents have no delete route yet; surface a hint instead of failing.
        onError("Documents can't be deleted individually yet. Use /api/profile to wipe all.");
        return;
      }
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Delete failed.");
    }
  }

  async function handleRefresh(s: UnifiedSource) {
    if (s.kind !== "web" && s.kind !== "github_user" && s.kind !== "github_repo") return;
    try {
      await refreshUrlSource(s.id);
      notifyChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Refresh failed.");
    }
  }

  return (
    <div className="space-y-4">
      {/* Upload zones */}
      <div className="grid gap-3 md:grid-cols-2">
        {/* CV PDF/DOCX */}
        <UploadCard
          title="CV file (PDF / DOCX)"
          subtitle="Parses into structured CV row. Auto-feeds master library."
          accept=".pdf,.docx"
          multiple
          busy={uploadingFiles}
          busyLabel="Parsing…"
          actionLabel="Upload CV files"
          onFiles={handleUploadCVs}
        />
        {/* Markdown CV (replaces master directly) */}
        <UploadCard
          title="Markdown CV (cv.md)"
          subtitle="Deterministic parse. Replaces master library wholesale."
          accept=".md,.markdown,.txt"
          busy={uploadingMd}
          busyLabel="Parsing…"
          actionLabel="Upload cv.md"
          onFiles={(fs) => fs[0] && handleUploadMarkdown(fs[0])}
        />
        {/* Free-form documents */}
        <UploadCard
          title="Supplementary documents"
          subtitle="Project notes, certificates, transcripts. Skills + projects merged into master."
          accept=".pdf,.docx,.txt,.md"
          multiple
          busy={uploadingDoc}
          busyLabel="Parsing…"
          actionLabel="Upload documents"
          onFiles={handleUploadDocument}
        />
        {/* Portfolio / GitHub URL */}
        <div className="space-y-2 rounded-lg border border-sky-300 bg-sky-50 p-3">
          <div>
            <p className="text-sm font-semibold text-sky-900">Portfolio URL or GitHub</p>
            <p className="text-xs text-sky-700">
              Generic site → scrape + LLM extract. github.com/user → list public repos.
              github.com/user/repo → README + metadata.
            </p>
          </div>
          <div className="flex gap-2">
            <input
              type="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="https://danielz.co.uk or https://github.com/danialza"
              className="flex-1 rounded-md border border-sky-200 bg-white px-2 py-1.5 text-xs focus:border-sky-500 focus:outline-none"
              disabled={addingUrl}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAddUrl();
              }}
            />
            <button
              type="button"
              onClick={handleAddUrl}
              disabled={addingUrl || !urlInput.trim()}
              className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
            >
              {addingUrl ? "Adding…" : "Add URL"}
            </button>
          </div>
        </div>
      </div>

      {/* Source list */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Sources ({sources.length})
          </p>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="text-xs text-slate-600 underline hover:text-slate-900 disabled:opacity-50"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
        {sources.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
            No sources yet. Drop a CV, paste a portfolio URL, or upload documents above.
            Every add auto-rebuilds the master CV that powers matching + tailoring.
          </p>
        ) : (
          <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
            {sources.map((s) => (
              <li key={`${s.kind}-${s.id}`} className="flex items-center gap-3 px-3 py-2">
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1 ${
                    KIND_COLOR[s.kind] || "bg-slate-100 text-slate-700 ring-slate-200"
                  }`}
                >
                  {KIND_LABEL[s.kind] || s.kind}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-800">{s.title}</p>
                  <p className="truncate text-[11px] text-slate-500">{s.detail}</p>
                  {s.error && (
                    <p className="truncate text-[11px] text-rose-600">⚠ {s.error}</p>
                  )}
                </div>
                <span
                  className={`shrink-0 text-[10px] font-semibold ${
                    s.status === "done"
                      ? "text-emerald-700"
                      : s.status === "pending"
                      ? "text-amber-700"
                      : "text-rose-700"
                  }`}
                >
                  {s.status}
                </span>
                {(s.kind === "web" || s.kind === "github_user" || s.kind === "github_repo") && (
                  <button
                    type="button"
                    onClick={() => handleRefresh(s)}
                    className="shrink-0 rounded border border-slate-200 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
                  >
                    Refresh
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleDelete(s)}
                  className="shrink-0 text-[11px] text-rose-600 hover:underline"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// Reusable upload card — drag-and-drop OR click-to-pick.
function UploadCard({
  title,
  subtitle,
  accept,
  multiple = false,
  busy,
  busyLabel,
  actionLabel,
  onFiles,
}: {
  title: string;
  subtitle: string;
  accept: string;
  multiple?: boolean;
  busy: boolean;
  busyLabel: string;
  actionLabel: string;
  onFiles: (files: File[]) => void;
}) {
  return (
    <label className="flex cursor-pointer flex-col gap-1 rounded-lg border border-dashed border-slate-300 bg-white p-3 hover:border-brand-400 hover:bg-brand-50/30">
      <p className="text-sm font-semibold text-slate-800">{title}</p>
      <p className="text-xs text-slate-500">{subtitle}</p>
      <input
        type="file"
        accept={accept}
        multiple={multiple}
        className="sr-only"
        onChange={(e) => {
          const fs = Array.from(e.target.files || []);
          if (fs.length) onFiles(fs);
          e.target.value = "";
        }}
        disabled={busy}
      />
      <span
        className={`mt-1 inline-flex w-fit rounded-md px-3 py-1 text-xs font-semibold ${
          busy
            ? "bg-slate-300 text-slate-600"
            : "bg-slate-900 text-white hover:bg-slate-800"
        }`}
      >
        {busy ? busyLabel : actionLabel}
      </span>
    </label>
  );
}
