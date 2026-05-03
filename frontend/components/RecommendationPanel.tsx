import type { JobParsed, MatchResult } from "@/lib/types";

interface Props {
  job: JobParsed;
  best: MatchResult | null;
}

export default function RecommendationPanel({ job, best }: Props) {
  return (
    <div className="rounded-xl border border-brand-200 bg-gradient-to-br from-brand-50 to-white p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="section-title text-brand-700">Job</p>
          <h3 className="mt-1 text-lg font-semibold text-slate-900">
            {job.job_title || "Untitled role"}
          </h3>
          <p className="text-sm text-slate-600">
            {[job.company, job.location].filter(Boolean).join(" · ") || "—"}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {job.experience_level && (
              <span className="rounded-full bg-white px-2 py-0.5 ring-1 ring-slate-200">
                {job.experience_level}
              </span>
            )}
            {job.employment_type && (
              <span className="rounded-full bg-white px-2 py-0.5 ring-1 ring-slate-200">
                {job.employment_type}
              </span>
            )}
            {job.remote_type && (
              <span className="rounded-full bg-white px-2 py-0.5 ring-1 ring-slate-200">
                {job.remote_type}
              </span>
            )}
            {job.salary && (
              <span className="rounded-full bg-white px-2 py-0.5 ring-1 ring-slate-200">
                {job.salary}
              </span>
            )}
          </div>
        </div>

        {best && (
          <div className="rounded-lg bg-brand-600 px-4 py-3 text-white shadow-sm">
            <p className="text-xs uppercase tracking-wider opacity-80">Recommended CV</p>
            <p className="mt-0.5 font-semibold">{best.cv_name || best.filename}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums">
              {best.overall_score.toFixed(0)}
              <span className="text-sm font-normal opacity-80">/100</span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
