import type { RankedMatchResponse } from "@/lib/types";
import MissingSkills from "./MissingSkills";
import RecommendationPanel from "./RecommendationPanel";
import ScoreCard from "./ScoreCard";
import StrongestPoints from "./StrongestPoints";

interface Props {
  data: RankedMatchResponse;
}

export default function MatchResults({ data }: Props) {
  const { job, results, recommended_cv_id } = data;
  const best = results.find((r) => r.cv_id === recommended_cv_id) ?? results[0] ?? null;

  return (
    <div className="space-y-5">
      <RecommendationPanel job={job} best={best} />

      <div className="space-y-4">
        {results.map((r, idx) => {
          const isRecommended = r.cv_id === recommended_cv_id;
          return (
            <article key={r.cv_id} className="card space-y-5">
              <header className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
                      {idx + 1}
                    </span>
                    <h3 className="text-lg font-semibold text-slate-900">
                      {r.cv_name || r.filename}
                    </h3>
                    {isRecommended && (
                      <span className="rounded-full bg-brand-600 px-2 py-0.5 text-xs font-medium text-white">
                        Recommended
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500">{r.filename}</p>
                </div>
              </header>

              <p className="text-sm leading-relaxed text-slate-700">{r.explanation}</p>

              <ScoreCard
                overall={r.overall_score}
                skill={r.skill_score}
                semantic={r.semantic_score}
                experience={r.experience_score}
                education={r.education_score}
                project={r.project_score}
              />

              <MissingSkills matched={r.matched_skills} missing={r.missing_skills} />

              <StrongestPoints
                points={r.strongest_points}
                suggestions={r.improvement_suggestions}
              />

              {r.semantic_evidence.length > 0 && (
                <div>
                  <p className="section-title">Semantic evidence</p>
                  <ul className="mt-2 space-y-1.5 text-xs text-slate-600">
                    {r.top_semantic_matches.map((m, i) => (
                      <li
                        key={i}
                        className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
                      >
                        <span className="font-medium text-slate-500">[{m.kind}]</span>{" "}
                        {m.text}
                        <span className="ml-2 tabular-nums text-slate-400">
                          {(m.score * 100).toFixed(0)}%
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
