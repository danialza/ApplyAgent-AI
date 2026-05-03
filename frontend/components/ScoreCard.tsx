interface ScoreBarProps {
  label: string;
  value: number;
}

function tierColor(value: number): string {
  if (value >= 75) return "bg-emerald-500";
  if (value >= 50) return "bg-amber-500";
  return "bg-rose-500";
}

export function ScoreBar({ label, value }: ScoreBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-slate-600">{label}</span>
        <span className="tabular-nums text-slate-500">{clamped.toFixed(0)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full ${tierColor(clamped)} transition-all`}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  );
}

interface ScoreCardProps {
  overall: number;
  skill: number;
  semantic: number;
  experience: number;
  education: number;
  project: number;
}

export default function ScoreCard({
  overall,
  skill,
  semantic,
  experience,
  education,
  project,
}: ScoreCardProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <p className="section-title">Overall match</p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-slate-900">
            {overall.toFixed(0)}
            <span className="text-base font-normal text-slate-400">/100</span>
          </p>
        </div>
        <div
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            overall >= 75
              ? "bg-emerald-100 text-emerald-700"
              : overall >= 50
                ? "bg-amber-100 text-amber-700"
                : "bg-rose-100 text-rose-700"
          }`}
        >
          {overall >= 75 ? "Strong" : overall >= 50 ? "Moderate" : "Weak"}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <ScoreBar label="Skills (40%)" value={skill} />
        <ScoreBar label="Semantic (25%)" value={semantic} />
        <ScoreBar label="Experience (20%)" value={experience} />
        <ScoreBar label="Education (10%)" value={education} />
        <ScoreBar label="Projects (5%)" value={project} />
      </div>
    </div>
  );
}
