"use client";

import { useEffect, useState } from "react";
import {
  buildLibraryFromCV,
  fetchCVLibrary,
  fetchLLMStatus,
  listCVs,
  putCVLibrary,
  rebuildLibraryFromAll,
  renderCV,
  uploadMarkdownCV,
} from "@/lib/api";
import type { CV, CVLibrary, LLMStatus, RenderCVResponse } from "@/lib/types";

interface Props {
  onError: (message: string) => void;
}

const SECTION_LABELS: Record<string, string> = {
  selected_projects: "Selected projects",
  additional_projects: "Additional projects",
  experience: "Experience",
  publications: "Publications",
  certifications: "Certifications",
};

export default function TailoredCVPanel({ onError }: Props) {
  const [library, setLibrary] = useState<CVLibrary | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [jobText, setJobText] = useState("");
  const [maxSelected, setMaxSelected] = useState(4);
  const [maxAdditional, setMaxAdditional] = useState(3);
  const [maxExperience, setMaxExperience] = useState(4);
  const [compilePdf, setCompilePdf] = useState(true);
  // Default ON: most users hit "Render" expecting tailoring. If LLM is
  // disabled server-side, the render still succeeds (rule-based fallback)
  // and llm_skip_reason explains the gap in the error banner.
  const [useLlm, setUseLlm] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RenderCVResponse | null>(null);

  // Library editor state — toggleable JSON textarea so the user can paste
  // in new projects / publications / certs without leaving the page.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorJson, setEditorJson] = useState("");
  const [editorSaving, setEditorSaving] = useState(false);

  // Uploaded CVs available for one-click library re-seeding.
  const [cvs, setCvs] = useState<CV[]>([]);
  const [importingFrom, setImportingFrom] = useState<number | null>(null);

  // Markdown CV uploader state.
  const [uploadingMd, setUploadingMd] = useState(false);

  // LLM status surfaced as a small badge so the user can verify the
  // polish path is actually live, not silently falling back.
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);
  const [llmChecking, setLlmChecking] = useState(false);

  async function refreshLLMStatus() {
    setLlmChecking(true);
    try {
      setLlmStatus(await fetchLLMStatus());
    } catch (err) {
      onError(err instanceof Error ? err.message : "LLM status check failed.");
    } finally {
      setLlmChecking(false);
    }
  }

  async function handleMarkdownUpload(file: File) {
    setUploadingMd(true);
    try {
      const updated = await uploadMarkdownCV(file);
      setLibrary(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Markdown upload failed.");
    } finally {
      setUploadingMd(false);
    }
  }

  // Fetch LLM status on mount so the badge is accurate before first render.
  useEffect(() => {
    refreshLLMStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await listCVs();
        if (!cancelled) setCvs(list);
      } catch {
        // Silent — section 1 already surfaces upload errors.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleImportFromCV(cvId: number) {
    setImportingFrom(cvId);
    try {
      const updated = await buildLibraryFromCV(cvId);
      setLibrary(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Library import failed.");
    } finally {
      setImportingFrom(null);
    }
  }

  async function handleRebuildFromAll() {
    setImportingFrom(-1); // sentinel for "rebuilding all"
    try {
      const updated = await rebuildLibraryFromAll();
      setLibrary(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Library rebuild failed.");
    } finally {
      setImportingFrom(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const lib = await fetchCVLibrary();
        if (!cancelled) setLibrary(lib);
      } catch (err) {
        if (!cancelled) {
          onError(err instanceof Error ? err.message : "Failed to load CV library.");
        }
      } finally {
        if (!cancelled) setLoadingLibrary(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onError]);

  async function handleRender() {
    setBusy(true);
    setResult(null);
    try {
      const data = await renderCV({
        job_text: jobText.trim(),
        compile_pdf: compilePdf,
        max_selected_projects: maxSelected,
        max_additional_projects: maxAdditional,
        max_experience: maxExperience,
        use_llm: useLlm,
      });
      setResult(data);
      if (data.compile_error && !data.compiled) {
        // Surface as a soft warning — LaTeX still came through.
        onError(`PDF compile not available: ${data.compile_error}`);
      }
      if (useLlm && !data.used_llm && data.llm_skip_reason) {
        // LLM was requested but skipped — show why so the user can fix env.
        onError(`LLM polish skipped: ${data.llm_skip_reason}`);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Render failed.");
    } finally {
      setBusy(false);
    }
  }

  function downloadLatex() {
    if (!result) return;
    const name = (result.suggested_filename || "tailored_cv") + ".tex";
    download(name, result.latex, "text/plain;charset=utf-8");
  }

  function downloadPdf() {
    if (!result?.pdf_b64) return;
    const bytes = Uint8Array.from(atob(result.pdf_b64), (c) => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (result.suggested_filename || "tailored_cv") + ".pdf";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function openEditor() {
    if (!library) return;
    // Strip read-only fields before showing — same shape PUT expects.
    const { id: _id, updated_at: _updated, ...payload } = library;
    setEditorJson(JSON.stringify(payload, null, 2));
    setEditorOpen(true);
  }

  async function saveEditor() {
    setEditorSaving(true);
    try {
      const parsed = JSON.parse(editorJson);
      const updated = await putCVLibrary(parsed);
      setLibrary(updated);
      setEditorOpen(false);
    } catch (err) {
      if (err instanceof SyntaxError) {
        onError(`Library JSON is invalid: ${err.message}`);
      } else {
        onError(err instanceof Error ? err.message : "Failed to save library.");
      }
    } finally {
      setEditorSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      {/* LLM connectivity badge */}
      <LLMStatusBanner
        status={llmStatus}
        checking={llmChecking}
        onRecheck={refreshLLMStatus}
      />

      {/* Library status header */}
      <LibrarySummary
        library={library}
        loading={loadingLibrary}
        onEdit={openEditor}
      />

      {/* Markdown CV uploader — career-ops style, deterministic parse */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs">
        <div>
          <p className="font-medium text-violet-900">
            Upload Markdown CV (recommended)
          </p>
          <p className="text-violet-700">
            Use{" "}
            <a
              href="https://github.com/your-handle/ai-job-cv-matcher/blob/main/docs/cv_template.md"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              <code>docs/cv_template.md</code>
            </a>{" "}
            as a template — fill it in once, upload here. Deterministic
            parse, no PDF whitespace surprises.
          </p>
        </div>
        <label className="cursor-pointer rounded-md border border-violet-600 bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-violet-700">
          <input
            type="file"
            accept=".md,.markdown,.txt,text/markdown"
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleMarkdownUpload(f);
              e.target.value = "";
            }}
            disabled={uploadingMd}
          />
          {uploadingMd ? "Parsing…" : "Upload cv.md"}
        </label>
      </div>

      {/* Unified library controls — one button to re-aggregate everything */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
        <div>
          <p className="font-medium text-slate-700">
            Unified CV library
          </p>
          <p className="text-slate-500">
            The library auto-rebuilds from every upload (CV + Document).
            Click <em>Rebuild</em> if you just edited raw uploads and want
            a fresh merge.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRebuildFromAll}
          disabled={importingFrom !== null}
          className="rounded-md border border-brand-500 bg-brand-500 px-3 py-1.5 font-semibold text-white shadow-sm hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {importingFrom === -1
            ? "Rebuilding…"
            : `Rebuild from all uploads (${cvs.length} CV${cvs.length === 1 ? "" : "s"})`}
        </button>
      </div>

      {/* Per-CV import as a fallback for users who want a specific source */}
      {cvs.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-slate-600">
            Or import from a specific CV file …
          </summary>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {cvs.slice(0, 5).map((cv) => (
              <button
                key={cv.id}
                type="button"
                onClick={() => handleImportFromCV(cv.id)}
                disabled={importingFrom !== null}
                title="Replace library with structured data from this single CV only."
                className="rounded-md border border-slate-300 bg-white px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {importingFrom === cv.id
                  ? "Importing…"
                  : cv.name || cv.filename || `CV #${cv.id}`}
              </button>
            ))}
            {cvs.length > 5 && (
              <span className="text-slate-400">… {cvs.length - 5} more</span>
            )}
          </div>
        </details>
      )}

      {/* Editor (toggleable) */}
      {editorOpen && (
        <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs text-amber-800">
            Edit your library JSON below. The full schema is{" "}
            <code className="rounded bg-amber-100 px-1">CVLibraryBase</code> —
            see the backend README for the field shapes. Add as many projects /
            papers / certifications as you want; the renderer picks the best
            subset per job.
          </p>
          <textarea
            className="block h-64 w-full rounded-md border border-amber-300 bg-white p-2 font-mono text-xs text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
            value={editorJson}
            onChange={(e) => setEditorJson(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setEditorOpen(false)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={saveEditor}
              disabled={editorSaving}
              className="rounded-md bg-slate-900 px-3 py-1 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
            >
              {editorSaving ? "Saving…" : "Save library"}
            </button>
          </div>
        </div>
      )}

      {/* JD input */}
      <div className="space-y-2">
        <p className="section-title">Job description (optional)</p>
        <textarea
          rows={5}
          value={jobText}
          onChange={(e) => setJobText(e.target.value)}
          placeholder="Paste the JD to tailor the CV to this role. Leave empty to render a master CV with everything in your library."
          className="block w-full rounded-lg border border-slate-300 bg-white p-3 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
        />
      </div>

      {/* Knobs */}
      <div className="flex flex-wrap items-end gap-3">
        <NumberKnob label="Selected" value={maxSelected} setValue={setMaxSelected} max={20} />
        <NumberKnob label="Additional" value={maxAdditional} setValue={setMaxAdditional} max={20} />
        <NumberKnob label="Experience" value={maxExperience} setValue={setMaxExperience} max={20} />
        <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
          <input
            type="checkbox"
            checked={compilePdf}
            onChange={(e) => setCompilePdf(e.target.checked)}
            className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          Compile PDF (requires tectonic in container)
        </label>
        <label
          className="flex items-center gap-2 text-xs font-medium text-slate-600"
          title="Polish bullets + summary with LLM (career-ops style). Requires OPENAI_API_KEY in backend env. Falls back to rule-based on failure."
        >
          <input
            type="checkbox"
            checked={useLlm}
            onChange={(e) => setUseLlm(e.target.checked)}
            className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          Polish with LLM
        </label>
        <button
          type="button"
          onClick={handleRender}
          disabled={busy || loadingLibrary || !library}
          className="ml-auto inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Rendering…" : "Render tailored CV"}
        </button>
      </div>

      {!library && !loadingLibrary && (
        <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
          No CV library yet. Run{" "}
          <code className="rounded bg-slate-100 px-1">make seed-cv</code> on the
          host to seed the bundled sample, or click <em>Edit library</em> above
          and paste a fresh library JSON.
        </p>
      )}

      {/* Results */}
      {result && (
        <ResultPanel
          result={result}
          onDownloadLatex={downloadLatex}
          onDownloadPdf={downloadPdf}
        />
      )}
    </div>
  );
}

// ---------- LLM status banner ----------

function LLMStatusBanner({
  status,
  checking,
  onRecheck,
}: {
  status: LLMStatus | null;
  checking: boolean;
  onRecheck: () => void;
}) {
  let tone = "slate";
  let label = "LLM status: checking…";
  let detail = "";
  if (status) {
    if (status.reachable) {
      tone = "emerald";
      label = `LLM connected · ${status.model}`;
      detail = status.base_url || "(default OpenAI endpoint)";
    } else if (status.enabled) {
      tone = "amber";
      label = "LLM configured but unreachable";
      detail = status.error || "no response from the chat-completions endpoint";
    } else {
      tone = "slate";
      label = "LLM disabled (deterministic mode)";
      detail = status.error || "set USE_LLM_EXTRACTION=true + OPENAI_API_KEY";
    }
  }
  const toneClasses: Record<string, string> = {
    emerald: "border-emerald-300 bg-emerald-50 text-emerald-900",
    amber: "border-amber-300 bg-amber-50 text-amber-900",
    slate: "border-slate-200 bg-slate-50 text-slate-700",
  };
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs ${toneClasses[tone]}`}
    >
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-[11px] opacity-80">{detail}</p>
      </div>
      <button
        type="button"
        onClick={onRecheck}
        disabled={checking}
        className="rounded-md border border-current/30 bg-white px-2.5 py-1 text-xs font-medium hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {checking ? "Checking…" : "Re-check"}
      </button>
    </div>
  );
}


// ---------- Library summary ----------

function LibrarySummary({
  library,
  loading,
  onEdit,
}: {
  library: CVLibrary | null;
  loading: boolean;
  onEdit: () => void;
}) {
  if (loading) {
    return (
      <p className="rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-500">
        Loading library…
      </p>
    );
  }
  if (!library) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-sm">
        <span className="font-semibold text-slate-900">{library.header.name}</span>
        <span className="text-slate-500"> · {library.header.location}</span>
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-slate-600">
        <Stat label="Selected projects" value={library.selected_projects.length} />
        <Stat label="Additional" value={library.additional_projects.length} />
        <Stat label="Experience" value={library.experience.length} />
        <Stat label="Publications" value={library.publications.length} />
        <Stat label="Certifications" value={library.certifications.length} />
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
      >
        Edit library
      </button>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span>
      <span className="font-semibold text-slate-900 tabular-nums">{value}</span>
      <span className="ml-1">{label}</span>
    </span>
  );
}

// ---------- Knob ----------

function NumberKnob({
  label,
  value,
  setValue,
  max,
}: {
  label: string;
  value: number;
  setValue: (v: number) => void;
  max: number;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
      <span>{label}</span>
      <input
        type="number"
        min={0}
        max={max}
        value={value}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (Number.isFinite(n)) setValue(Math.min(max, Math.max(0, n)));
        }}
        className="w-20 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
    </label>
  );
}

// ---------- Result panel ----------

function ResultPanel({
  result,
  onDownloadLatex,
  onDownloadPdf,
}: {
  result: RenderCVResponse;
  onDownloadLatex: () => void;
  onDownloadPdf: () => void;
}) {
  return (
    <section className="space-y-4 rounded-lg border border-emerald-200 bg-emerald-50/50 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900">
          Rendered{" "}
          {result.compiled ? (
            <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">
              PDF ready
            </span>
          ) : (
            <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
              LaTeX only
            </span>
          )}
          {result.used_llm && (
            <span className="ml-2 rounded-full bg-violet-100 px-2 py-0.5 text-xs text-violet-700">
              LLM-polished
            </span>
          )}
          {result.matched_skills.length > 0 && (
            <span
              className="ml-2 rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-700"
              title={`Covered ${result.keywords_covered.length} of ${result.matched_skills.length} JD canonical terms in the rendered LaTeX.`}
            >
              {`Keyword coverage: ${Math.round(result.keyword_coverage * 100)}% (${result.keywords_covered.length}/${result.matched_skills.length})`}
            </span>
          )}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onDownloadLatex}
            className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Download .tex
          </button>
          {result.compiled && (
            <button
              type="button"
              onClick={onDownloadPdf}
              className="rounded-md bg-slate-900 px-2.5 py-1 text-xs font-semibold text-white hover:bg-slate-800"
            >
              Download .pdf
            </button>
          )}
        </div>
      </header>

      {result.matched_skills.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Matched skills (bolded inside bullets)
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {result.matched_skills.map((s) => (
              <span
                key={s}
                className="rounded-full bg-brand-50 px-2 py-0.5 text-xs text-brand-700 ring-1 ring-brand-200"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {Object.entries(result.sections_chosen).map(([key, items]) => {
          if (items.length === 0) return null;
          return (
            <div key={key}>
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                {SECTION_LABELS[key] || key}
              </p>
              <ul className="mt-1 space-y-0.5 text-xs text-slate-700">
                {items.map((t, i) => (
                  <li key={i} className="line-clamp-2">
                    · {t}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      {result.compile_error && (
        <p className="text-xs italic text-amber-700">{result.compile_error}</p>
      )}
    </section>
  );
}

// ---------- helpers ----------

function download(filename: string, data: string, mime: string) {
  const blob = new Blob([data], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
