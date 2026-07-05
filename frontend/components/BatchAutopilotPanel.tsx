"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  answerBatchQuestions,
  deleteFact,
  fetchBatch,
  fetchFacts,
  retryBatchItem,
  startBatch,
  type BatchItem,
  type CandidateFact,
} from "@/lib/api";

interface Props {
  onError: (message: string) => void;
  /** Bump the tracker panel after the batch files new applications. */
  onApplicationTracked?: () => void;
}

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600 ring-slate-200",
  fetching: "bg-sky-100 text-sky-700 ring-sky-200",
  rendering: "bg-violet-100 text-violet-700 ring-violet-200",
  needs_input: "bg-amber-100 text-amber-800 ring-amber-200",
  done: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  duplicate: "bg-slate-100 text-slate-500 ring-slate-200",
  failed: "bg-rose-100 text-rose-700 ring-rose-200",
};

const ACTIVE = new Set(["queued", "fetching", "rendering"]);

export default function BatchAutopilotPanel({ onError, onApplicationTracked }: Props) {
  const [urlText, setUrlText] = useState("");
  const [batchId, setBatchId] = useState("");
  const [items, setItems] = useState<BatchItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [facts, setFacts] = useState<CandidateFact[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevDone = useRef(0);

  const refresh = useCallback(async (id: string) => {
    try {
      const data = await fetchBatch(id);
      if (data.batch_id && !id) setBatchId(data.batch_id);
      setItems(data.items);
      // Nudge the tracker when new items finish.
      const doneCount = data.items.filter((i) => i.status === "done").length;
      if (doneCount > prevDone.current) onApplicationTracked?.();
      prevDone.current = doneCount;
    } catch {
      /* transient — keep polling */
    }
  }, [onApplicationTracked]);

  // Load the most recent batch + facts on mount.
  useEffect(() => {
    refresh("");
    fetchFacts().then(setFacts).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll while any item is still active.
  useEffect(() => {
    const anyActive = items.some((i) => ACTIVE.has(i.status));
    if (anyActive && !pollRef.current) {
      pollRef.current = setInterval(() => refresh(batchId), 2000);
    } else if (!anyActive && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
      fetchFacts().then(setFacts).catch(() => {});
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [items, batchId, refresh]);

  async function handleStart() {
    const urls = urlText
      .split(/[\n\s]+/)
      .map((u) => u.trim())
      .filter((u) => u.startsWith("http"));
    if (urls.length === 0) {
      onError("Paste at least one http(s) job URL.");
      return;
    }
    setBusy(true);
    try {
      const data = await startBatch(urls);
      setBatchId(data.batch_id);
      setItems(data.items);
      prevDone.current = 0;
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to start batch.");
    } finally {
      setBusy(false);
    }
  }

  // Aggregate unique pending questions across all needs_input items.
  const pending: { key: string; question: string }[] = [];
  const seen = new Set<string>();
  for (const it of items) {
    if (it.status !== "needs_input") continue;
    for (const q of it.pending_questions || []) {
      if (!seen.has(q.key)) {
        seen.add(q.key);
        pending.push(q);
      }
    }
  }

  async function handleAnswer() {
    const payload = pending
      .map((q) => ({ key: q.key, question: q.question, answer: (answers[q.key] || "").trim() }))
      .filter((a) => a.answer);
    if (payload.length === 0) {
      onError("Answer at least one question.");
      return;
    }
    setBusy(true);
    try {
      await answerBatchQuestions(payload);
      setAnswers({});
      await refresh(batchId);
      await fetchFacts().then(setFacts);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to save answers.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRetry(id: number) {
    try {
      await retryBatchItem(id);
      await refresh(batchId);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Retry failed.");
    }
  }

  async function handleForget(key: string) {
    try {
      await deleteFact(key);
      setFacts((f) => f.filter((x) => x.key !== key));
    } catch {
      /* ignore */
    }
  }

  const doneN = items.filter((i) => i.status === "done").length;

  return (
    <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">7. Batch autopilot</h2>
        <p className="mt-1 text-sm text-slate-500">
          Paste job links. It fetches each JD, tailors a CV, and files it in the
          tracker — moving to the next automatically. When it needs something it
          can&apos;t infer, it asks you once and remembers your answer for every
          future job.
        </p>
      </div>

      <textarea
        value={urlText}
        onChange={(e) => setUrlText(e.target.value)}
        rows={4}
        placeholder={"https://…job1\nhttps://…job2\nhttps://…job3"}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono"
      />
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleStart}
          disabled={busy}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
        >
          {busy ? "Working…" : "Run autopilot"}
        </button>
        {items.length > 0 && (
          <span className="text-xs text-slate-500">
            {doneN}/{items.length} filed
            {items.some((i) => ACTIVE.has(i.status)) && " · working…"}
          </span>
        )}
      </div>

      {/* Questions inbox */}
      {pending.length > 0 && (
        <div className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm font-semibold text-amber-900">
            ⚠ Needs your input — answer once, reused forever
          </p>
          {pending.map((q) => (
            <label key={q.key} className="block text-xs">
              <span className="font-medium text-amber-900">{q.question}</span>
              <input
                type="text"
                value={answers[q.key] || ""}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.key]: e.target.value }))}
                placeholder="Your answer…"
                className="mt-1 w-full rounded border border-amber-300 px-2 py-1"
              />
            </label>
          ))}
          <button
            type="button"
            onClick={handleAnswer}
            disabled={busy}
            className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-60"
          >
            Save answers &amp; continue
          </button>
        </div>
      )}

      {/* Queue table */}
      {items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-50 text-left font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-1.5">Where?</th>
                <th className="px-2 py-1.5">What?</th>
                <th className="px-2 py-1.5">Status</th>
                <th className="px-2 py-1.5">Coverage</th>
                <th className="px-2 py-1.5">Link</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((i) => (
                <tr key={i.id}>
                  <td className="px-2 py-1.5 font-medium text-slate-800">{i.company || "—"}</td>
                  <td className="px-2 py-1.5 text-slate-700">{i.role || "—"}</td>
                  <td className="px-2 py-1.5">
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ring-1 ${
                        STATUS_STYLE[i.status] || "bg-slate-100 text-slate-600 ring-slate-200"
                      }`}
                      title={i.error || ""}
                    >
                      {i.status}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    {i.keyword_coverage >= 0 ? `${Math.round(i.keyword_coverage * 100)}%` : "—"}
                  </td>
                  <td className="max-w-[16rem] truncate px-2 py-1.5">
                    <a href={i.url} target="_blank" rel="noreferrer" className="text-brand-700 underline">
                      {i.url}
                    </a>
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {(i.status === "failed" || i.status === "needs_input") && (
                      <button
                        type="button"
                        onClick={() => handleRetry(i.id)}
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50"
                      >
                        Retry
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Learned facts */}
      <details className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs">
        <summary className="cursor-pointer select-none font-medium text-slate-600">
          Learned facts · memory{facts.length > 0 && ` (${facts.length})`}
        </summary>
        <div className="mt-2 space-y-1">
          {facts.length === 0 && (
            <p className="text-slate-500">
              Nothing yet. Answers you give above are saved here and reused for future jobs.
            </p>
          )}
          {facts.map((f) => (
            <div key={f.key} className="flex items-start gap-2 rounded bg-white px-2 py-1">
              <div className="flex-1">
                <div className="font-medium text-slate-700">{f.question || f.key}</div>
                <div className="text-slate-600">{f.answer}</div>
              </div>
              <button
                type="button"
                onClick={() => handleForget(f.key)}
                className="shrink-0 text-rose-500 hover:text-rose-700"
                title="Forget this fact"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}
