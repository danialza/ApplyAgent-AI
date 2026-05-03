"use client";

import { useEffect, useState } from "react";
import AgentDashboard from "@/components/AgentDashboard";
import CVUpload from "@/components/CVUpload";
import ErrorMessage from "@/components/ErrorMessage";
import LoadingState from "@/components/LoadingState";
import ManualJobInput from "@/components/ManualJobInput";
import MatchResults from "@/components/MatchResults";
import UploadedCVList from "@/components/UploadedCVList";
import { listCVs, matchAll } from "@/lib/api";
import type { CV, RankedMatchResponse } from "@/lib/types";

export default function HomePage() {
  const [cvs, setCvs] = useState<CV[]>([]);
  const [loadingCvs, setLoadingCvs] = useState(true);
  const [matching, setMatching] = useState(false);
  const [results, setResults] = useState<RankedMatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Initial load: pull existing CVs (so refresh keeps state).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const fetched = await listCVs();
        if (!cancelled) setCvs(fetched);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load CVs.");
        }
      } finally {
        if (!cancelled) setLoadingCvs(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function handleUploaded(created: CV[]) {
    setCvs((prev) => [...created, ...prev]);
    setError(null);
  }

  function handleDeleted(cvId: number) {
    setCvs((prev) => prev.filter((c) => c.id !== cvId));
    // Invalidate stale results that reference the removed CV.
    if (results?.results.some((r) => r.cv_id === cvId)) setResults(null);
  }

  async function handleAnalyse(jobText: string) {
    setMatching(true);
    setError(null);
    try {
      const data = await matchAll(jobText);
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Match request failed.");
    } finally {
      setMatching(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6 lg:px-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
          AI Job-CV Matching Agent
        </h1>
        <p className="text-base text-slate-600">
          Upload CVs, paste a job description, and find the best match.
        </p>
      </header>

      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      <section className="card space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">1. Upload CVs</h2>
          <p className="text-sm text-slate-500">
            PDF or DOCX. You can compare multiple candidates against a single role.
          </p>
        </div>

        <CVUpload onUploaded={handleUploaded} onError={setError} />

        <div className="space-y-2">
          <p className="section-title">Uploaded CVs</p>
          {loadingCvs ? (
            <LoadingState label="Loading CVs…" />
          ) : (
            <UploadedCVList cvs={cvs} onDelete={handleDeleted} onError={setError} />
          )}
        </div>
      </section>

      <section className="card space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">2. Add a job description</h2>
          <p className="text-sm text-slate-500">
            Pick the input that fits — paste, link, file, or bulk CSV.
          </p>
        </div>

        <ManualJobInput
          onAnalyse={handleAnalyse}
          onError={setError}
          busy={matching}
          disabled={cvs.length === 0}
          disabledReason={
            cvs.length === 0 ? "Upload at least one CV to enable matching." : undefined
          }
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-slate-900">3. Results</h2>
        {matching ? (
          <div className="card">
            <LoadingState label="Scoring CVs against the job description…" />
          </div>
        ) : results ? (
          <MatchResults data={results} />
        ) : (
          <div className="rounded-xl border border-dashed border-slate-200 bg-white px-6 py-10 text-center text-sm text-slate-500">
            Results will appear here once you analyse a job description.
          </div>
        )}
      </section>

      <section className="card space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">4. Agent run (full pipeline)</h2>
          <p className="text-sm text-slate-500">
            One click runs profile aggregation → smart query generation → job
            discovery from public APIs → ranking against your CVs → tailoring
            suggestions for the top matches.
          </p>
        </div>
        <AgentDashboard
          onError={setError}
          disabled={cvs.length === 0}
          disabledReason={
            cvs.length === 0
              ? "Upload at least one CV to enable the agent."
              : undefined
          }
        />
      </section>

      <footer className="pt-6 text-center text-xs text-slate-400">
        Backend: FastAPI · NLP: sentence-transformers + FAISS
      </footer>
    </main>
  );
}
