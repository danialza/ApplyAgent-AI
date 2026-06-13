"use client";

import { useEffect, useState } from "react";
import {
  checkDuplicateApplication,
  convertCVTextToMarkdown,
  createApplication,
  deleteCVLibrary,
  fetchCVLibrary,
  fetchCVTemplate,
  fetchLLMStatus,
  putCVLibrary,
  renderCV,
  uploadMarkdownCV,
} from "@/lib/api";
import type {
  CVLibrary,
  DuplicateCheck,
  LLMStatus,
  RenderCVResponse,
} from "@/lib/types";

interface Props {
  onError: (message: string) => void;
  /** Bump tracker panel after we create or skip an application. */
  onApplicationTracked?: () => void;
}

const SECTION_LABELS: Record<string, string> = {
  projects: "Projects",
  experience: "Experience",
  publications: "Publications",
  certifications: "Certifications",
};

export default function TailoredCVPanel({ onError, onApplicationTracked }: Props) {
  const [library, setLibrary] = useState<CVLibrary | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [jobText, setJobText] = useState("");
  // Page-target picker — drives section caps. The numeric overrides
  // below are -1 by default, meaning "follow whatever the planner
  // picked". Power users can flip the Advanced disclosure to pin
  // exact numbers.
  const [targetLength, setTargetLength] = useState<
    "auto" | "one_page" | "one_half_page" | "two_page"
  >("auto");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxSelected, setMaxSelected] = useState(-1);
  const [maxAdditional, setMaxAdditional] = useState(-1);
  const [maxExperience, setMaxExperience] = useState(-1);
  const [compilePdf, setCompilePdf] = useState(true);
  // Default ON: most users hit "Render" expecting tailoring. If LLM is
  // disabled server-side, the render still succeeds (rule-based fallback)
  // and llm_skip_reason explains the gap in the error banner.
  const [useLlm, setUseLlm] = useState(true);
  const [enhanceTailor, setEnhanceTailor] = useState(false);
  // Manual per-job project pick. Empty set = automatic ranking.
  const [pinnedTitles, setPinnedTitles] = useState<string[]>([]);
  // true = LLM ranks + trims within the pick; false = force all picks.
  const [pinnedRank, setPinnedRank] = useState(true);
  // Coverage auto-boost target. Renderer loops the LLM up to 3 times
  // weaving missing JD keywords into existing bullets until coverage
  // hits this fraction (or the loop stalls).
  const [coverageTarget, setCoverageTarget] = useState(0.8);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RenderCVResponse | null>(null);

  // Library editor state — toggleable JSON textarea so the user can paste
  // in new projects / publications / certs without leaving the page.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorJson, setEditorJson] = useState("");
  const [editorSaving, setEditorSaving] = useState(false);

  // Uploaded CVs available for one-click library re-seeding.

  // Markdown CV uploader state.

  // In-app LLM converter: paste raw CV text → cv.md, then push into
  // the library with one click. Same prompt as docs/cv_to_markdown_prompt.md.
  const [converterInput, setConverterInput] = useState("");
  const [converterOutput, setConverterOutput] = useState("");
  const [converting, setConverting] = useState(false);
  const [seedingFromConverter, setSeedingFromConverter] = useState(false);

  async function handleConvertCVText() {
    if (!converterInput.trim()) {
      onError("Paste some CV text first.");
      return;
    }
    setConverting(true);
    setConverterOutput("");
    try {
      const { markdown } = await convertCVTextToMarkdown(converterInput);
      setConverterOutput(markdown);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Conversion failed.");
    } finally {
      setConverting(false);
    }
  }

  async function handleSeedFromConverter() {
    if (!converterOutput.trim()) return;
    setSeedingFromConverter(true);
    try {
      const blob = new File([converterOutput], "cv.md", {
        type: "text/markdown",
      });
      const updated = await uploadMarkdownCV(blob);
      setLibrary(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Seeding library failed.");
    } finally {
      setSeedingFromConverter(false);
    }
  }

  async function handleCopyConverterOutput() {
    try {
      await navigator.clipboard.writeText(converterOutput);
    } catch {
      onError("Clipboard copy blocked by the browser.");
    }
  }

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

  async function switchLLMProvider(p: "claude_code" | "anthropic" | "openai") {
    setLlmChecking(true);
    try {
      const { setLLMProvider } = await import("@/lib/api");
      setLlmStatus(await setLLMProvider(p));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Switch failed.");
    } finally {
      setLlmChecking(false);
    }
  }

  async function switchLLMModel(model: string) {
    if (!model.trim()) return;
    setLlmChecking(true);
    try {
      const { setLLMModel } = await import("@/lib/api");
      setLlmStatus(await setLLMModel(model.trim()));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Model switch failed.");
    } finally {
      setLlmChecking(false);
    }
  }

  async function handleDownloadTemplate() {
    try {
      const { filename, content } = await fetchCVTemplate();
      download(filename, content, "text/markdown;charset=utf-8");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not fetch template.");
    }
  }

  async function handleResetLibrary() {
    if (!confirm("Delete the current CV library? CVs + Documents stay; only the parsed library is removed.")) {
      return;
    }
    try {
      await deleteCVLibrary();
      setLibrary(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Reset failed.");
    }
  }

  // Heuristic: detect a library that was auto-built from a PDF and looks
  // garbled (words mashed together). When triggered we show an amber
  // banner pushing the user toward the markdown path.
  const looksLossy = library
    ? detectLossyLibrary(library)
    : false;

  // Fetch LLM status on mount so the badge is accurate before first render.
  useEffect(() => {
    refreshLLMStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // Dedupe state — re-checked before every render so the "already
  // tracked" banner reflects the JD currently in the textarea.
  const [duplicate, setDuplicate] = useState<DuplicateCheck | null>(null);
  // Once we've actually tracked / skipped this render, hide the
  // tracker buttons to avoid double-inserts.
  const [trackedForThisRender, setTrackedForThisRender] = useState(false);

  async function refreshDuplicateCheck(jdText: string, url: string) {
    if (!jdText.trim() && !url.trim()) {
      setDuplicate(null);
      return;
    }
    try {
      const d = await checkDuplicateApplication({ jd_text: jdText, url });
      setDuplicate(d.matched ? d : null);
    } catch {
      // Silent — dedupe is advisory, not blocking.
      setDuplicate(null);
    }
  }

  async function handleTrack(skip: boolean) {
    if (!result) return;
    // Attach the tailored CV snapshot for non-Skipped rows. For
    // Skipped rows there's no point storing the CV — saves DB bytes.
    const payload = {
      apply_date: skip ? "" : new Date().toISOString().slice(0, 10),
      deadline: "",
      company: result.job_company || "",
      role: result.job_title || "",
      status: skip ? "Skipped" : "To-Apply",
      how: "",
      url: extractUrlFromText(jobText) || "",
      notes: "",
      jd_text: jobText.trim(),
      cv_latex: skip ? "" : result.latex || "",
      cv_pdf_b64: skip ? "" : result.pdf_b64 || "",
      cv_filename: skip ? "" : result.suggested_filename || "tailored-cv",
    };
    try {
      await createApplication(payload);
      setTrackedForThisRender(true);
      onApplicationTracked?.();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Track failed.");
    }
  }

  async function handleRender() {
    setBusy(true);
    setResult(null);
    setTrackedForThisRender(false);
    // Dedupe check is now BLOCKING — when the JD is already tracked
    // we stop and require explicit user confirmation before burning
    // LLM + LaTeX cycles re-rendering it. Skip the prompt when the
    // textarea is empty (the check would no-op anyway).
    const jdt = jobText.trim();
    const jurl = extractUrlFromText(jobText) || "";
    if (jdt || jurl) {
      try {
        const d = await checkDuplicateApplication({ jd_text: jdt, url: jurl });
        setDuplicate(d.matched ? d : null);
        if (d.matched && d.application) {
          const app = d.application;
          const ok = confirm(
            `Already tracked.\n\n` +
            `${app.role || "(no role)"} @ ${app.company || "(no company)"}\n` +
            `Added: ${app.apply_date || "?"} · Status: ${app.status || "?"}\n\n` +
            `Render again anyway?`
          );
          if (!ok) {
            setBusy(false);
            return;
          }
        }
      } catch {
        // Dedupe check itself failed — fall through and render rather
        // than block the user on an advisory check.
      }
    } else {
      setDuplicate(null);
    }
    try {
      const data = await renderCV({
        job_text: jobText.trim(),
        compile_pdf: compilePdf,
        max_selected_projects: maxSelected,
        max_additional_projects: maxAdditional,
        max_experience: maxExperience,
        use_llm: useLlm,
        // Always include every stretch skill; the dropdown was removed
        // because the LLM Core Competencies + coverage-boost loop now
        // decide what surfaces, not a per-skill threshold.
        min_competency_rating: 1,
        target_length: targetLength,
        target_keyword_coverage: coverageTarget,
        enhance_tailor: enhanceTailor,
        pinned_project_titles: pinnedTitles,
        pinned_rank: pinnedRank,
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
        onSwitch={switchLLMProvider}
        onSwitchModel={switchLLMModel}
      />

      {/* Library status header */}
      <LibrarySummary
        library={library}
        loading={loadingLibrary}
        onEdit={openEditor}
      />

      {/* Lossy-PDF warning when auto-seed produced garbage */}
      {looksLossy && (
        <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-semibold">
            ⚠ This library looks like it was auto-built from a PDF, which is
            unreliable on real-world CVs.
          </p>
          <p>
            PDF text extraction drops inter-word spacing on many fonts, so
            projects get misclassified into Education / Skills and the
            tailored CV reads garbled.
          </p>
          <p>
            <strong>Fix in 30 seconds:</strong> download the markdown
            template, fill it in (it&apos;s already structured), upload
            here. Career-ops uses the same approach.
          </p>
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={handleDownloadTemplate}
              className="rounded-md border border-amber-600 bg-white px-2.5 py-1 font-semibold text-amber-900 hover:bg-amber-100"
            >
              Download cv_template.md
            </button>
            <button
              type="button"
              onClick={handleResetLibrary}
              className="rounded-md border border-rose-300 bg-white px-2.5 py-1 font-semibold text-rose-700 hover:bg-rose-50"
            >
              Reset library
            </button>
          </div>
        </div>
      )}

      {/* Master CV source-of-truth banner — section 5 is read-only on
          top of what section 1 produces. No duplicate uploaders here. */}
      <div className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
        <p>
          <strong>Reads from the master CV built in section 1.</strong>{" "}
          Add or remove sources (CV files, docs, portfolio URLs, GitHub)
          there and the master rebuilds automatically. This section just
          tailors the master to a JD.
        </p>
      </div>

      {/* LLM-powered converter — paste any CV, get cv.md, push to master.
          Collapsed by default so it doesn't crowd the primary tailoring
          flow; expand when you want a one-off conversion. */}
      <details className="rounded-lg border border-indigo-300 bg-indigo-50 p-3 text-xs">
        <summary className="cursor-pointer font-semibold text-indigo-900">
          ▾ Advanced · Convert raw CV text → cv.md via LLM
        </summary>
        <div className="mt-2 space-y-2">
          <p className="text-indigo-700">
            Paste raw CV text (PDF copy-paste, LinkedIn export, free
            notes). Same prompt as <code>docs/cv_to_markdown_prompt.md</code>
            — runs through your{" "}
            {llmStatus?.provider
              ? `${llmStatus.provider} · ${llmStatus.model}`
              : "configured LLM"}
            .
          </p>
          {!llmStatus?.reachable && (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-amber-900">
              LLM not reachable. Set <code>USE_LLM_EXTRACTION=true</code> and
              an API key in <code>.env</code>, then restart the backend.
            </p>
          )}
          <textarea
            value={converterInput}
            onChange={(e) => setConverterInput(e.target.value)}
            rows={8}
            placeholder="Paste your existing CV text here — any format works."
            className="w-full rounded-md border border-indigo-200 bg-white px-2 py-1.5 font-mono text-slate-800 focus:border-indigo-500 focus:outline-none"
            disabled={converting}
          />
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleConvertCVText}
              disabled={converting || !converterInput.trim()}
              className="rounded-md border border-indigo-600 bg-indigo-600 px-3 py-1.5 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {converting ? "Converting…" : "Convert with LLM"}
            </button>
            <button
              type="button"
              onClick={() => {
                setConverterInput("");
                setConverterOutput("");
              }}
              disabled={converting}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50"
            >
              Clear
            </button>
          </div>
          {converterOutput && (
            <div className="space-y-2 rounded-md border border-indigo-200 bg-white p-2">
              <p className="font-semibold text-indigo-900">
                cv.md preview — edit if needed, then push to master
              </p>
              <textarea
                value={converterOutput}
                onChange={(e) => setConverterOutput(e.target.value)}
                rows={14}
                className="w-full rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 font-mono text-slate-800 focus:border-indigo-500 focus:outline-none"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleSeedFromConverter}
                  disabled={seedingFromConverter}
                  className="rounded-md border border-violet-600 bg-violet-600 px-3 py-1.5 font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {seedingFromConverter ? "Pushing…" : "Replace master with this"}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    download(
                      "cv.md",
                      converterOutput,
                      "text/markdown;charset=utf-8"
                    )
                  }
                  className="rounded-md border border-indigo-400 bg-white px-3 py-1.5 font-semibold text-indigo-900 hover:bg-indigo-50"
                >
                  Download cv.md
                </button>
                <button
                  type="button"
                  onClick={handleCopyConverterOutput}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Copy
                </button>
              </div>
            </div>
          )}
        </div>
      </details>

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

      {/* Page-target picker — replaces guess-the-numbers knobs. The
          planner clamps everything against the library size, so e.g.
          "two pages" never claims 5 projects when you only have 3. */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600">Target length</span>
          <div className="inline-flex rounded-lg border border-slate-300 bg-white p-0.5 text-xs font-semibold shadow-sm">
            {([
              ["one_page", "1 page"],
              ["one_half_page", "1.5 pages"],
              ["two_page", "2 pages"],
              ["auto", "Auto (LLM)"],
            ] as const).map(([val, lbl]) => (
              <button
                key={val}
                type="button"
                onClick={() => setTargetLength(val)}
                className={
                  "rounded-md px-2.5 py-1 transition " +
                  (targetLength === val
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100")
                }
                title={
                  val === "auto"
                    ? "LLM picks caps from JD seniority + your library. Falls back to 2 pages if LLM is off."
                    : val === "one_page"
                    ? "Junior / early-career: 2 selected · 1 additional · 2 experience"
                    : val === "one_half_page"
                    ? "Mid-level tight: 3 selected · 1 additional · 2 experience"
                    : "Senior / mid: 4 selected · 2 additional · 3 experience"
                }
              >
                {lbl}
              </button>
            ))}
          </div>
        </div>
        {result?.section_plan && (
          <p className="max-w-xs text-[11px] text-slate-500">
            Planner chose{" "}
            <span className="font-semibold text-slate-700">
              {result.section_plan.max_selected_projects}/
              {result.section_plan.max_additional_projects}/
              {result.section_plan.max_experience}
            </span>{" "}
            ({result.section_plan.source}).{" "}
            {result.section_plan.rationale}
          </p>
        )}
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
        <label
          className={`flex items-center gap-2 text-xs font-medium ${
            enhanceTailor ? "text-amber-700" : "text-slate-600"
          }`}
          title="ENHANCE mode — lets the LLM add JD-relevant skills the library doesn't list, expand project bullets, and rewrite experience bullets more freely. Trade-off: higher JD-fit, higher fabrication risk. Off by default. Requires Polish with LLM."
        >
          <input
            type="checkbox"
            checked={enhanceTailor}
            disabled={!useLlm}
            onChange={(e) => setEnhanceTailor(e.target.checked)}
            className="rounded border-slate-300 text-amber-600 focus:ring-amber-500"
          />
          Enhance tailor ⚠
        </label>
        <label
          className="flex items-center gap-2 text-xs font-medium text-slate-600"
          title="If coverage falls below this target, the renderer asks the LLM to weave missing JD keywords into existing bullets and re-renders. Loops up to 3 rounds. Set to 0% to disable."
        >
          <span>Coverage target</span>
          <select
            value={coverageTarget}
            onChange={(e) => setCoverageTarget(Number(e.target.value))}
            className="rounded border-slate-300 bg-white px-1.5 py-0.5 text-xs"
          >
            {[0, 0.6, 0.7, 0.8, 0.9, 0.95].map((n) => (
              <option key={n} value={n}>
                {Math.round(n * 100)}%
              </option>
            ))}
          </select>
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

      {/* Advanced override — pin exact counts and skip the planner for
          that field. -1 means "follow planner". */}
      <details
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
        className="rounded-lg border border-slate-200 bg-white p-2 text-xs"
      >
        <summary className="cursor-pointer select-none font-medium text-slate-600">
          Advanced · override section caps
        </summary>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <p className="basis-full text-[11px] text-slate-500">
            Set any field to <code>-1</code> to follow the target-length
            planner, or pin an exact count. Pinned fields skip the
            planner for that section.
          </p>
          <NumberKnob
            label="Selected (-1 = auto)"
            value={maxSelected}
            setValue={setMaxSelected}
            min={-1}
            max={20}
          />
          <NumberKnob
            label="Additional (-1 = auto)"
            value={maxAdditional}
            setValue={setMaxAdditional}
            min={-1}
            max={20}
          />
          <NumberKnob
            label="Experience (-1 = auto)"
            value={maxExperience}
            setValue={setMaxExperience}
            min={-1}
            max={20}
          />
        </div>
      </details>

      {/* Manual project pick — override the automatic ranker per job.
          Empty selection = automatic. Order follows click order. */}
      {library &&
        (library.selected_projects.length > 0 ||
          library.additional_projects.length > 0) && (
          <details className="rounded-lg border border-slate-200 bg-white p-2 text-xs">
            <summary className="cursor-pointer select-none font-medium text-slate-600">
              Projects · pick manually{" "}
              {pinnedTitles.length > 0 && (
                <span className="ml-1 rounded-full bg-brand-100 px-1.5 py-0.5 text-[10px] font-semibold text-brand-800">
                  {pinnedTitles.length} pinned
                </span>
              )}
            </summary>
            <div className="mt-2 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-[11px] text-slate-500">
                  Tick the projects to consider for this CV. Nothing ticked =
                  every project is in play (automatic JD ranking).
                </p>
                {pinnedTitles.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setPinnedTitles([])}
                    className="shrink-0 rounded border border-slate-300 bg-white px-2 py-0.5 font-medium text-slate-600 hover:bg-slate-50"
                  >
                    Clear (auto)
                  </button>
                )}
              </div>
              {pinnedTitles.length > 0 && (
                <label className="flex items-start gap-2 rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] text-slate-600">
                  <input
                    type="checkbox"
                    checked={pinnedRank}
                    onChange={(e) => setPinnedRank(e.target.checked)}
                    className="mt-0.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span>
                    <span className="font-semibold text-slate-700">
                      Let the LLM rank & trim within my picks
                    </span>
                    <br />
                    {pinnedRank
                      ? "On — only these projects are considered, then the JD ranker orders them and the page-fit cap keeps the best. Recommended."
                      : "Off — force ALL ticked projects, in tick order, no ranking or trimming."}
                  </span>
                </label>
              )}
              {[
                ...library.selected_projects.map((p) => ({
                  title: p.title,
                  group: "Selected",
                })),
                ...library.additional_projects.map((p) => ({
                  title: p.title,
                  group: "Additional",
                })),
              ]
                .filter((x) => (x.title || "").trim())
                .map((x) => {
                  const idx = pinnedTitles.indexOf(x.title);
                  const checked = idx >= 0;
                  return (
                    <label
                      key={x.title}
                      className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          setPinnedTitles((prev) =>
                            e.target.checked
                              ? [...prev, x.title]
                              : prev.filter((t) => t !== x.title)
                          );
                        }}
                        className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                      />
                      {checked && !pinnedRank && (
                        <span className="w-4 shrink-0 text-center text-[10px] font-bold text-brand-700">
                          {idx + 1}
                        </span>
                      )}
                      <span className="text-slate-700">{x.title}</span>
                      <span className="ml-auto rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                        {x.group}
                      </span>
                    </label>
                  );
                })}
            </div>
          </details>
        )}

      {!library && !loadingLibrary && (
        <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
          No CV library yet. Run{" "}
          <code className="rounded bg-slate-100 px-1">make seed-cv</code> on the
          host to seed the bundled sample, or click <em>Edit library</em> above
          and paste a fresh library JSON.
        </p>
      )}

      {/* Duplicate-application warning — shown when the JD or its URL
          matches a row we already tracked. Advisory; does NOT block
          rendering, since the user might want to update an existing
          application or re-render a tailored CV. */}
      {duplicate && duplicate.matched && duplicate.application && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-semibold">
            ⚠ Already tracked ({duplicate.match_kind.replace("_", " ")})
          </p>
          <p>
            You added this on{" "}
            <strong>
              {duplicate.application.apply_date ||
                duplicate.application.created_at.slice(0, 10)}
            </strong>{" "}
            as <strong>{duplicate.application.role || "—"}</strong> at{" "}
            <strong>{duplicate.application.company || "—"}</strong> ·
            current status:{" "}
            <strong>{duplicate.application.status}</strong>.
          </p>
        </div>
      )}

      {/* Results */}
      {result && (
        <ResultPanel
          result={result}
          onDownloadLatex={downloadLatex}
          onDownloadPdf={downloadPdf}
        />
      )}

      {/* Tracker actions — only after a successful render with a JD.
          Master-CV renders (no JD) have nothing to track. */}
      {result && jobText.trim() && !trackedForThisRender && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs">
          <p className="text-slate-700">
            Track this job?{" "}
            {result.job_title || result.job_company ? (
              <>
                <strong>{result.job_title || "—"}</strong> at{" "}
                <strong>{result.job_company || "—"}</strong>
              </>
            ) : (
              <span className="text-slate-500">(JD title not parsed)</span>
            )}
          </p>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => handleTrack(false)}
              className="rounded-md border border-emerald-600 bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
            >
              Track this application
            </button>
            <button
              type="button"
              onClick={() => handleTrack(true)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              title="Mark as Skipped so the dedupe check warns you next time, but keep it out of your active list."
            >
              Not interested
            </button>
          </div>
        </div>
      )}
      {trackedForThisRender && (
        <p className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs text-emerald-800">
          ✓ Added to tracker (section 6).
        </p>
      )}
    </div>
  );
}

// Pull the first http(s) URL out of pasted JD text. Many job boards
// paste the URL above or below the description; treating that as
// the application URL lets the dedupe check work even before the
// user fills in the tracker row.
function extractUrlFromText(text: string): string {
  const m = (text || "").match(/https?:\/\/[^\s)\]]+/i);
  return m ? m[0] : "";
}

// ---------- LLM status banner ----------

function LLMStatusBanner({
  status,
  checking,
  onRecheck,
  onSwitch,
  onSwitchModel,
}: {
  status: LLMStatus | null;
  checking: boolean;
  onRecheck: () => void;
  onSwitch: (p: "claude_code" | "anthropic" | "openai") => void;
  onSwitchModel: (m: string) => void;
}) {
  // Known models per provider — fetched lazily on first render.
  const [models, setModels] = useState<Record<string, string[]>>({});
  useEffect(() => {
    import("@/lib/api").then(({ fetchKnownModels }) =>
      fetchKnownModels().then(setModels).catch(() => undefined)
    );
  }, []);
  const providerModels = (status?.provider && models[status.provider]) || [];
  let tone = "slate";
  let label = "LLM status: checking…";
  let detail = "";
  if (status) {
    if (status.reachable) {
      tone = "emerald";
      label = `LLM connected · ${status.provider || "?"} · ${status.model}`;
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
      <div className="flex items-center gap-1.5">
        {/* Provider switcher. claude_code (Pro CLI) is interactive-
            only — Anthropic doesn't authorise headless `claude --print`
            with a Pro/Max subscription, so it can't power an
            automated backend. Kept selectable only when already
            active so the user isn't stuck; not offered as a fresh
            pick. anthropic / openai are the real choices. */}
        <select
          value={status?.provider || ""}
          onChange={(e) =>
            onSwitch(e.target.value as "claude_code" | "anthropic" | "openai")
          }
          disabled={checking}
          className="rounded-md border border-current/30 bg-white px-1.5 py-1 text-xs font-medium disabled:opacity-50"
          title="Only providers with an API key in .env are listed. Add a key + restart to enable more."
        >
          {(() => {
            // Only offer providers that actually have a key. Empty
            // list → show a disabled hint instead of dead options.
            const avail = status?.available_providers || [];
            if (avail.length === 0) {
              return <option value="">No API key configured</option>;
            }
            return avail.map((p) => (
              <option key={p} value={p}>
                {p === "anthropic" ? "Anthropic API" : p === "openai" ? "OpenAI API" : p}
              </option>
            ));
          })()}
          {status?.provider === "claude_code" && (
            <option value="claude_code">Claude SDK (Pro) — headless unsupported</option>
          )}
        </select>
        {/* Model dropdown — known IDs per provider + free-text. */}
        {providerModels.length > 0 && (
          <select
            value={status?.model || ""}
            onChange={(e) => {
              const val = e.target.value;
              if (val === "__custom__") {
                const m = prompt("Enter model ID (e.g. claude-sonnet-4-7-preview):", status?.model || "");
                if (m && m.trim()) onSwitchModel(m.trim());
              } else if (val) {
                onSwitchModel(val);
              }
            }}
            disabled={checking}
            className="rounded-md border border-current/30 bg-white px-1.5 py-1 text-xs font-medium disabled:opacity-50"
            title="Switch model. Custom = type any ID for unreleased/preview models."
          >
            {!providerModels.includes(status?.model || "") && status?.model && (
              <option value={status.model}>{status.model} (current)</option>
            )}
            {providerModels.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
            <option value="__custom__">Custom…</option>
          </select>
        )}
        <button
          type="button"
          onClick={onRecheck}
          disabled={checking}
          className="rounded-md border border-current/30 bg-white px-2.5 py-1 text-xs font-medium hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {checking ? "Checking…" : "Re-check"}
        </button>
      </div>
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
        <Stat
          label="Projects"
          value={library.selected_projects.length + library.additional_projects.length}
        />
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
  min = 0,
}: {
  label: string;
  value: number;
  setValue: (v: number) => void;
  max: number;
  min?: number;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (Number.isFinite(n)) setValue(Math.min(max, Math.max(min, n)));
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
          {result.coverage_iterations && result.coverage_iterations > 0 && (
            <span
              className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800"
              title={(result.coverage_boost_log || []).join("\n")}
            >
              {`Boosted ${result.coverage_iterations}× · ${(result.coverage_history || [])
                .map((c) => `${Math.round(c * 100)}%`)
                .join(" → ")}`}
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

      {result.core_competencies && result.core_competencies.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Core competencies (LLM-synthesised, career-ops grid)
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {result.core_competencies.map((c) => (
              <span
                key={c}
                className="rounded-md bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-800 ring-1 ring-teal-200"
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

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

/**
 * Heuristic — does the library look like it came from a lossy PDF parse?
 * Signals: a skill-group called "Other" that's much larger than every
 * normal group, OR a degree string with no internal whitespace > 25 chars,
 * OR an experience company starting with a comma. Conservative — we'd
 * rather miss a warning than wrongly accuse a clean markdown upload.
 */
function detectLossyLibrary(library: CVLibrary): boolean {
  const otherGroup = library.skills_groups.find(
    (g) => g.label.toLowerCase() === "other",
  );
  const normalSize = library.skills_groups
    .filter((g) => g.label.toLowerCase() !== "other")
    .reduce((max, g) => Math.max(max, g.items.length), 0);
  if (otherGroup && otherGroup.items.length > Math.max(20, normalSize * 4)) {
    return true;
  }
  for (const e of library.education) {
    const flat = (e.institution || "").replace(/\s+/g, "");
    if (flat.length > 40 && /[a-z][A-Z]/.test(e.institution || "")) {
      return true; // CamelCase-without-spaces is a pdfplumber signature
    }
  }
  for (const x of library.experience) {
    if ((x.company || "").trim().startsWith(",")) return true;
  }
  return false;
}


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
