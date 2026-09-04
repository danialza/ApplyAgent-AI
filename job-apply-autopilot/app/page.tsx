'use client';

import { SyntheticEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Bot,
  BrainCircuit,
  BriefcaseBusiness,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  ExternalLink,
  FileDown,
  FileText,
  LoaderCircle,
  MonitorUp,
  Play,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRoundCheck,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import {
  AGENT_BASE,
  AgentHealth,
  AgentSettings,
  ApplicationRun,
  CvOptions,
  MemoryFact,
  answerQuestions,
  createRun,
  cvUrl,
  fetchHealth,
  fetchCvOptions,
  fetchMemory,
  fetchRun,
  fetchRuns,
  fetchSettings,
  forgetMemory,
  putSettings,
  runAction,
} from '@/lib/agent-api';

const STAGES = [
  { key: 'opening', label: 'Opening job link', icon: ExternalLink },
  { key: 'reading_job', label: 'Reading job description', icon: BriefcaseBusiness },
  { key: 'tailoring_cv', label: 'Tailoring your CV', icon: FileText },
  { key: 'filling_form', label: 'Filling the application', icon: Bot },
  { key: 'ready_for_review', label: 'Ready for your review', icon: UserRoundCheck },
] as const;

const STAGE_INDEX: Record<string, number> = {
  queued: -1,
  opening: 0,
  reading_job: 1,
  tailoring_cv: 2,
  entering_application: 3,
  filling_form: 3,
  needs_input: 3,
  needs_user_action: 3,
  ready_for_review: 4,
  failed: 3,
};

const STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Working',
  needs_input: 'Needs your answer',
  needs_user_action: 'Needs your action',
  ready_for_review: 'Ready to review',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const CV_PROVIDER_LABELS: Record<AgentSettings['cv_llm_provider'], string> = {
  claude_code: 'Claude subscription',
  anthropic: 'Anthropic API',
  openai: 'OpenAI API',
};

const CV_MODEL_FALLBACKS: Record<AgentSettings['cv_llm_provider'], string[]> = {
  claude_code: ['sonnet', 'opus', 'haiku'],
  anthropic: ['claude-sonnet-5', 'claude-opus-5', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
  openai: ['gpt-5', 'gpt-5-mini', 'gpt-4o', 'gpt-4o-mini'],
};

type ModelContextTool = {
  name: string;
  title?: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations?: Record<string, unknown>;
  execute: (input: { url: string }) => Promise<Record<string, unknown>>;
};

type ModelContext = {
  registerTool: (tool: ModelContextTool, options?: { signal?: AbortSignal }) => void | Promise<void>;
};

function formatTime(value: string) {
  if (!value) return '';
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`);
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function displayRun(run: ApplicationRun) {
  if (run.company && run.role) return `${run.role} · ${run.company}`;
  if (run.role) return run.role;
  try {
    return new URL(run.url).hostname.replace(/^www\./, '');
  } catch {
    return 'New application';
  }
}

function statusTone(status: ApplicationRun['status']) {
  if (status === 'ready_for_review') return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300';
  if (status === 'needs_input' || status === 'needs_user_action') return 'border-amber-400/30 bg-amber-400/10 text-amber-200';
  if (status === 'failed') return 'border-red-400/30 bg-red-400/10 text-red-300';
  if (status === 'running') return 'border-cyan-400/30 bg-cyan-400/10 text-cyan-200';
  return 'border-border bg-muted text-muted-foreground';
}

export default function Home() {
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [runs, setRuns] = useState<ApplicationRun[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<ApplicationRun | null>(null);
  const [memory, setMemory] = useState<MemoryFact[]>([]);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [draftSettings, setDraftSettings] = useState<AgentSettings | null>(null);
  const [cvOptions, setCvOptions] = useState<CvOptions | null>(null);
  const [url, setUrl] = useState('');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [error, setError] = useState('');
  const selectedIdRef = useRef('');

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  const loadRuns = useCallback(async (preferredId?: string) => {
    const nextRuns = await fetchRuns();
    setRuns(nextRuns);
    const targetId = preferredId || selectedIdRef.current || nextRuns[0]?.id || '';
    if (targetId) {
      setSelectedId(targetId);
      setSelected(await fetchRun(targetId));
    } else {
      setSelected(null);
    }
  }, []);

  const refreshSideData = useCallback(async () => {
    const [nextHealth, nextMemory] = await Promise.all([fetchHealth(), fetchMemory()]);
    setHealth(nextHealth);
    setMemory(nextMemory);
  }, []);

  useEffect(() => {
    Promise.all([fetchSettings(), loadRuns(), refreshSideData()])
      .then(([nextSettings]) => {
        setSettings(nextSettings);
        setDraftSettings(nextSettings);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Could not connect to the local agent.'));
    fetchCvOptions().then(setCvOptions).catch(() => undefined);
  }, [loadRuns, refreshSideData]);

  useEffect(() => {
    if (!settingsOpen) return;
    fetchCvOptions().then(setCvOptions).catch(() => undefined);
  }, [settingsOpen]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadRuns().catch(() => undefined);
      refreshSideData().catch(() => undefined);
    }, 3500);
    return () => window.clearInterval(timer);
  }, [loadRuns, refreshSideData]);

  useEffect(() => {
    if (!selectedId) return;
    const stream = new EventSource(`${AGENT_BASE}/api/runs/${selectedId}/events`);
    const refresh = () => {
      loadRuns(selectedId).catch(() => undefined);
      refreshSideData().catch(() => undefined);
    };
    stream.addEventListener('run', refresh);
    stream.addEventListener('event', refresh);
    stream.addEventListener('question', refresh);
    return () => stream.close();
  }, [selectedId, loadRuns, refreshSideData]);

  useEffect(() => {
    const pending = selected?.questions?.filter((question) => question.status === 'pending') || [];
    setAnswers((current) => {
      const next = { ...current };
      for (const question of pending) {
        if (!(question.id in next)) next[question.id] = '';
      }
      return next;
    });
  }, [selected?.questions]);

  const beginRun = useCallback(async (jobUrl: string) => {
    const trimmed = jobUrl.trim();
    if (!trimmed) throw new Error('Paste a job application link first.');
    setStarting(true);
    setError('');
    try {
      const run = await createRun(trimmed);
      setSelectedId(run.id);
      setSelected(run);
      setUrl('');
      await loadRuns(run.id);
      return run;
    } finally {
      setStarting(false);
    }
  }, [loadRuns]);

  useEffect(() => {
    const modelContext = (document as Document & { modelContext?: ModelContext }).modelContext;
    if (!modelContext?.registerTool) return;
    const lifecycle = new AbortController();
    try {
      void Promise.resolve(modelContext.registerTool({
        name: 'start_job_application',
        title: 'Start job application',
        description: 'Start a visible job application run from an application URL and stop before final submission.',
        inputSchema: {
          type: 'object',
          properties: { url: { type: 'string', description: 'The full job application URL.' } },
          required: ['url'],
          additionalProperties: false,
        },
        annotations: { readOnlyHint: false, untrustedContentHint: false },
        execute: async ({ url: jobUrl }) => {
          const run = await beginRun(jobUrl);
          return { runId: run.id, status: run.status, message: 'Application run started.' };
        },
      }, { signal: lifecycle.signal })).catch((cause) => {
        if (!lifecycle.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Could not register the application tool.');
        }
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not register the application tool.');
    }
    return () => lifecycle.abort();
  }, [beginRun]);

  const onStart = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      await beginRun(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start the application.');
    }
  };

  const selectRun = async (id: string) => {
    setSelectedId(id);
    setError('');
    try {
      setSelected(await fetchRun(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load this application.');
    }
  };

  const act = async (action: 'resume' | 'cancel' | 'focus') => {
    if (!selected) return;
    setBusyAction(action);
    setError('');
    try {
      await runAction(selected.id, action);
      await loadRuns(selected.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not ${action} this application.`);
    } finally {
      setBusyAction('');
    }
  };

  const submitAnswers = async () => {
    if (!selected) return;
    const pending = selected.questions?.filter((question) => question.status === 'pending') || [];
    const missing = pending.find((question) => !answers[question.id]?.trim());
    if (missing) {
      setError(`Please answer: ${missing.question}`);
      return;
    }
    setBusyAction('answers');
    setError('');
    try {
      await answerQuestions(selected.id, pending.map((question) => ({
        id: question.id,
        answer: answers[question.id].trim(),
      })));
      await Promise.all([loadRuns(selected.id), refreshSideData()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save your answers.');
    } finally {
      setBusyAction('');
    }
  };

  const saveSettings = async () => {
    if (!draftSettings) return;
    setSaving(true);
    setError('');
    try {
      const saved = await putSettings({
        cv_api_base: draftSettings.cv_api_base,
        llm_mode: draftSettings.llm_mode,
        llm_model: draftSettings.llm_model,
        cv_length: draftSettings.cv_length,
        cv_compile_pdf: draftSettings.cv_compile_pdf,
        cv_use_llm: draftSettings.cv_use_llm,
        cv_enhance_tailor: draftSettings.cv_enhance_tailor,
        cv_coverage_target: draftSettings.cv_coverage_target,
        cv_llm_provider: draftSettings.cv_llm_provider,
        cv_llm_model: draftSettings.cv_llm_model,
      });
      setSettings(saved);
      setDraftSettings(saved);
      setSettingsOpen(false);
      await refreshSideData();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save settings.');
    } finally {
      setSaving(false);
    }
  };

  const removeMemory = async (key: string) => {
    try {
      await forgetMemory(key);
      setMemory(await fetchMemory());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not forget this answer.');
    }
  };

  const pendingQuestions = selected?.questions?.filter((question) => question.status === 'pending') || [];
  const activeStage = STAGE_INDEX[selected?.stage || selected?.status || 'queued'] ?? -1;
  const latestByStage = useMemo(() => {
    const result = new Map<string, string>();
    for (const event of selected?.events || []) result.set(event.stage, event.message);
    return result;
  }, [selected?.events]);
  const availableCvProviders = useMemo(() => {
    const available = cvOptions?.status.available_providers || [];
    const current = draftSettings?.cv_llm_provider;
    const values = available.length ? available : current ? [current] : ['anthropic'];
    return values.filter((provider): provider is AgentSettings['cv_llm_provider'] => provider in CV_PROVIDER_LABELS);
  }, [cvOptions, draftSettings?.cv_llm_provider]);
  const availableCvModels = useMemo(() => {
    const provider = draftSettings?.cv_llm_provider || 'anthropic';
    const models = cvOptions?.models[provider] || CV_MODEL_FALLBACKS[provider];
    const current = draftSettings?.cv_llm_model;
    return current && !models.includes(current) ? [current, ...models] : models;
  }, [cvOptions, draftSettings?.cv_llm_model, draftSettings?.cv_llm_provider]);

  return (
    <main className="min-h-screen text-foreground">
      <header className="sticky top-0 z-40 border-b border-white/10 bg-background/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-5 py-3 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-cyan-300">
              <Sparkles className="size-5" />
            </div>
            <div>
              <div className="font-semibold tracking-tight">ApplyPilot</div>
              <div className="text-xs text-muted-foreground">Local job application copilot</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className={cn('hidden gap-1.5 sm:flex', health?.ok && health.cv_service ? 'border-emerald-400/30 text-emerald-300' : 'border-red-400/30 text-red-300')}>
              <span className={cn('size-1.5 rounded-full', health?.ok && health.cv_service ? 'bg-emerald-400' : 'bg-red-400')} />
              {health?.ok && health.cv_service ? 'Systems online' : 'Agent offline'}
            </Badge>
            <Badge variant="outline" className="hidden gap-1.5 border-amber-400/30 text-amber-200 md:flex">
              <ShieldCheck className="size-3.5" /> Human submits
            </Badge>
            <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
              <Settings className="size-4" /> Settings
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid min-w-0 max-w-[1600px] grid-cols-[minmax(0,1fr)] gap-5 px-5 py-5 lg:grid-cols-[280px_minmax(0,1fr)] lg:px-8 xl:grid-cols-[280px_minmax(0,1fr)_300px]">
        <aside className="order-2 min-w-0 lg:order-1">
          <Card className="border-white/10 bg-card/75">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">Applications</CardTitle>
                <Badge variant="secondary">{runs.length}</Badge>
              </div>
              <CardDescription>Recent local runs</CardDescription>
            </CardHeader>
            <CardContent className="px-2 pb-2">
              <ScrollArea className="h-[330px] lg:h-[calc(100vh-185px)]">
                <div className="space-y-1 pr-2">
                  {runs.length === 0 && (
                    <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-muted-foreground">
                      Your first application will appear here.
                    </div>
                  )}
                  {runs.map((run) => (
                    <button
                      key={run.id}
                      type="button"
                      onClick={() => selectRun(run.id)}
                      className={cn(
                        'w-full rounded-xl border px-3 py-3 text-left transition-colors',
                        selectedId === run.id
                          ? 'border-cyan-400/35 bg-cyan-400/10'
                          : 'border-transparent hover:border-white/10 hover:bg-white/[0.035]',
                      )}
                    >
                      <div className="line-clamp-2 text-sm font-medium">{displayRun(run)}</div>
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <Badge variant="outline" className={cn('max-w-[170px] truncate text-[10px]', statusTone(run.status))}>
                          {run.status === 'running' && <LoaderCircle className="mr-1 size-3 animate-spin" />}
                          {STATUS_LABELS[run.status] || run.status}
                        </Badge>
                        <span className="text-[11px] text-muted-foreground">{formatTime(run.updated_at)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </aside>

        <section className="order-1 min-w-0 space-y-5 lg:order-2">
          <Card className="overflow-hidden border-cyan-400/20 bg-card/80 shadow-[0_0_55px_-25px_rgba(34,211,238,0.4)]">
            <CardHeader>
              <CardTitle className="text-xl">Start a job application</CardTitle>
              <CardDescription>
                Paste the application link. A visible Chrome window will open, tailor your CV, and fill the form.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={onStart} className="flex flex-col gap-2 sm:flex-row">
                <Input
                  type="url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://company.com/jobs/apply/..."
                  className="h-11 flex-1 bg-background/60"
                  aria-label="Job application URL"
                />
                <Button type="submit" size="lg" disabled={starting || !health?.ok} className="h-11 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
                  {starting ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4 fill-current" />}
                  Start applying
                </Button>
              </form>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><MonitorUp className="size-3.5 text-cyan-300" /> You see every browser step</span>
                <span className="flex items-center gap-1.5"><BrainCircuit className="size-3.5 text-violet-300" /> Unknown answers are remembered</span>
                <span className="flex items-center gap-1.5"><ShieldCheck className="size-3.5 text-emerald-300" /> Final Submit is always yours</span>
              </div>
            </CardContent>
          </Card>

          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-red-400/25 bg-red-400/10 px-4 py-3 text-sm text-red-200">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              <span className="flex-1">{error}</span>
              <button type="button" onClick={() => setError('')} className="text-red-200/70 hover:text-red-100">Dismiss</button>
            </div>
          )}

          {!selected ? (
            <Card className="border-white/10 bg-card/60">
              <CardContent className="flex min-h-[360px] flex-col items-center justify-center px-8 text-center">
                <div className="mb-4 flex size-14 items-center justify-center rounded-2xl border border-white/10 bg-white/5">
                  <Bot className="size-7 text-cyan-300" />
                </div>
                <h2 className="text-lg font-semibold">Ready when you are</h2>
                <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                  Your Master CV and saved answers stay connected to the existing service, while this application agent runs separately.
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              <Card className="border-white/10 bg-card/70">
                <CardHeader className="pb-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <CardTitle className="truncate text-lg">{displayRun(selected)}</CardTitle>
                      <CardDescription className="mt-1 truncate">{selected.url}</CardDescription>
                    </div>
                    <Badge variant="outline" className={cn('w-fit shrink-0', statusTone(selected.status))}>
                      {selected.status === 'running' && <LoaderCircle className="mr-1.5 size-3.5 animate-spin" />}
                      {STATUS_LABELS[selected.status] || selected.status}
                    </Badge>
                  </div>
                  <div className="pt-3">
                    <div className="mb-1.5 flex justify-between text-xs text-muted-foreground">
                      <span>Application progress</span><span>{selected.progress}%</span>
                    </div>
                    <Progress value={selected.progress} className="h-1.5" />
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-1">
                    {STAGES.map((step, index) => {
                      const done = index < activeStage || selected.status === 'ready_for_review';
                      const current = index === activeStage && !done;
                      const Icon = step.icon;
                      const message = latestByStage.get(step.key);
                      return (
                        <div key={step.key} className="grid grid-cols-[34px_minmax(0,1fr)] gap-3">
                          <div className="flex flex-col items-center">
                            <div className={cn(
                              'flex size-8 items-center justify-center rounded-full border',
                              done && 'border-emerald-400/35 bg-emerald-400/10 text-emerald-300',
                              current && 'border-cyan-400/40 bg-cyan-400/10 text-cyan-200',
                              !done && !current && 'border-white/10 bg-white/[0.025] text-muted-foreground',
                            )}>
                              {done ? <Check className="size-4" /> : current ? <LoaderCircle className="size-4 animate-spin" /> : <Icon className="size-4" />}
                            </div>
                            {index < STAGES.length - 1 && <div className={cn('my-1 h-7 w-px', done ? 'bg-emerald-400/35' : 'bg-white/10')} />}
                          </div>
                          <div className="min-w-0 pt-1">
                            <div className={cn('text-sm font-medium', !done && !current && 'text-muted-foreground')}>{step.label}</div>
                            {(message || current) && (
                              <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{message || 'Working…'}</div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              {selected.status === 'needs_input' && pendingQuestions.length > 0 && (
                <Card className="border-amber-400/25 bg-amber-400/[0.06]">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base text-amber-100">
                      <AlertCircle className="size-5" /> I need your answer
                    </CardTitle>
                    <CardDescription>These answers will be remembered and reused on future applications.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {pendingQuestions.map((question) => (
                      <div key={question.id} className="space-y-2">
                        <label htmlFor={question.id} className="text-sm font-medium">{question.question}</label>
                        {question.input_type === 'select' || question.input_type === 'boolean' ? (
                          <Select value={answers[question.id] || null} onValueChange={(value) => setAnswers((current) => ({ ...current, [question.id]: String(value) }))}>
                            <SelectTrigger id={question.id} className="h-10 w-full bg-background/60">
                              <SelectValue placeholder="Choose an answer" />
                            </SelectTrigger>
                            <SelectContent>
                              {(question.input_type === 'boolean'
                                ? [{ value: 'Yes', label: 'Yes' }, { value: 'No', label: 'No' }]
                                : question.options
                              ).map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                            </SelectContent>
                          </Select>
                        ) : (
                          <Input
                            id={question.id}
                            value={answers[question.id] || ''}
                            onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
                            className="bg-background/60"
                          />
                        )}
                      </div>
                    ))}
                    <Button onClick={submitAnswers} disabled={busyAction === 'answers'}>
                      {busyAction === 'answers' ? <LoaderCircle className="size-4 animate-spin" /> : <BrainCircuit className="size-4" />}
                      Save answers and continue
                    </Button>
                  </CardContent>
                </Card>
              )}

              {selected.status === 'needs_user_action' && (
                <Card className="border-amber-400/25 bg-amber-400/[0.06]">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base text-amber-100"><MonitorUp className="size-5" /> Your action is needed in Chrome</CardTitle>
                    <CardDescription>Complete the sign-in, verification, or CAPTCHA in the visible browser, then continue.</CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-wrap gap-2">
                    <Button variant="outline" onClick={() => act('focus')} disabled={Boolean(busyAction)}><ExternalLink className="size-4" /> Show Chrome</Button>
                    <Button onClick={() => act('resume')} disabled={Boolean(busyAction)}>
                      {busyAction === 'resume' ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} I completed it — continue
                    </Button>
                  </CardContent>
                </Card>
              )}

              {selected.status === 'ready_for_review' && (
                <Card className="border-emerald-400/30 bg-emerald-400/[0.07] shadow-[0_0_40px_-25px_rgba(52,211,153,0.7)]">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg text-emerald-200"><CheckCircle2 className="size-5" /> Ready for your final review</CardTitle>
                    <CardDescription>The CV is ready and the form is filled. ApplyPilot stopped before the final Submit button.</CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-wrap gap-2">
                    <Button onClick={() => act('focus')} disabled={Boolean(busyAction)} className="bg-emerald-300 text-slate-950 hover:bg-emerald-200">
                      <ExternalLink className="size-4" /> Review form in Chrome
                    </Button>
                    {selected.has_cv && (
                      <a
                        className={buttonVariants({ variant: 'outline' })}
                        href={cvUrl(selected.id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <FileDown className="size-4" /> Open tailored CV
                      </a>
                    )}
                  </CardContent>
                </Card>
              )}

              {selected.status === 'failed' && (
                <Card className="border-red-400/25 bg-red-400/[0.06]">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base text-red-200"><AlertCircle className="size-5" /> This run needs attention</CardTitle>
                    <CardDescription>{selected.error || 'The application could not continue.'}</CardDescription>
                  </CardHeader>
                  <CardContent className="flex gap-2">
                    <Button variant="outline" onClick={() => act('focus')}><ExternalLink className="size-4" /> Show Chrome</Button>
                    <Button onClick={() => act('resume')}><RefreshCw className="size-4" /> Try again</Button>
                  </CardContent>
                </Card>
              )}

              {(selected.events?.length || 0) > 0 && (
                <Card className="border-white/10 bg-card/60">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Live activity</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {(selected.events || []).slice(-6).reverse().map((event) => (
                        <div key={event.id} className="flex gap-3 text-sm">
                          <div className="mt-1.5 size-1.5 shrink-0 rounded-full bg-cyan-300" />
                          <div className="min-w-0 flex-1">
                            <div className="text-foreground/90">{event.message}</div>
                            <div className="mt-0.5 text-xs text-muted-foreground">{formatTime(event.created_at)}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </section>

        <aside className="order-3 min-w-0 space-y-5 lg:col-span-2 xl:col-span-1">
          <Card className="border-white/10 bg-card/70">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">System setup</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <SetupRow label="Application agent" ready={Boolean(health?.agent)} detail="Port 8500" />
              <SetupRow label="Master CV service" ready={Boolean(health?.cv_service)} detail="Shared data · Port 8400" />
              <SetupRow label="Form AI" ready={Boolean(health?.llm?.configured)} detail={settings?.llm_mode === 'claude_subscription' ? `Claude subscription · ${settings.llm_model}` : `Anthropic API · ${settings?.llm_model || ''}`} />
              <SetupRow label="CV model" ready={settings?.cv_use_llm === 'false' || Boolean(cvOptions?.status.configured)} detail={settings?.cv_use_llm === 'false' ? 'LLM polish off' : `${CV_PROVIDER_LABELS[settings?.cv_llm_provider || 'anthropic']} · ${settings?.cv_llm_model || ''}`} />
              <SetupRow label="Final submission" ready detail="Always manual" />
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-card/70">
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-sm">Answer memory</CardTitle>
                  <CardDescription className="mt-1">Used in future forms</CardDescription>
                </div>
                <Badge variant="secondary">{memory.length}</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {memory.slice(0, 5).map((fact) => (
                  <div key={fact.memory_key} className="rounded-lg border border-white/8 bg-white/[0.025] px-3 py-2">
                    <div className="truncate text-xs text-muted-foreground">{fact.question || fact.memory_key}</div>
                    <div className="mt-0.5 truncate text-sm font-medium">{fact.answer}</div>
                  </div>
                ))}
                {memory.length === 0 && <p className="py-2 text-sm text-muted-foreground">Answers you teach the agent will appear here.</p>}
              </div>
              <Button variant="ghost" size="sm" className="mt-3 w-full" onClick={() => setMemoryOpen(true)}>
                <BrainCircuit className="size-4" /> Manage memory <ChevronRight className="ml-auto size-4" />
              </Button>
            </CardContent>
          </Card>

          <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-4 py-3 text-xs leading-5 text-amber-100/80">
            ApplyPilot can prepare applications and navigate multi-step forms, but it never clicks the final submission button.
          </div>
        </aside>
      </div>

      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="border-white/10 bg-card sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>ApplyPilot settings</SheetTitle>
            <SheetDescription>Changes apply to the next application run.</SheetDescription>
          </SheetHeader>
          {draftSettings && (
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 pb-4">
              <div>
                <h3 className="text-sm font-semibold">Application form AI</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">Used to understand and fill fields on application pages.</p>
              </div>
              <div className="space-y-2">
                <label htmlFor="llm-mode" className="text-sm font-medium">Connection</label>
                <Select value={draftSettings.llm_mode} onValueChange={(value) => setDraftSettings({ ...draftSettings, llm_mode: value as AgentSettings['llm_mode'] })}>
                  <SelectTrigger id="llm-mode" className="h-10 w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="claude_subscription">Claude monthly subscription</SelectItem>
                    <SelectItem value="anthropic_api">Anthropic API key</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs leading-5 text-muted-foreground">
                  Subscription mode uses the signed-in Claude command locally. API mode is the fallback and may have usage charges.
                </p>
              </div>
              <div className="space-y-2">
                <label htmlFor="model" className="text-sm font-medium">Form model</label>
                <Input id="model" value={draftSettings.llm_model} onChange={(event) => setDraftSettings({ ...draftSettings, llm_model: event.target.value })} />
              </div>
              <Separator />
              <div>
                <h3 className="text-sm font-semibold">CV generation</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">These controls are sent to the CV renderer for every new application.</p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="cv-provider" className="text-sm font-medium">CV provider</label>
                  <Select
                    value={draftSettings.cv_llm_provider}
                    disabled={draftSettings.cv_use_llm === 'false'}
                    onValueChange={(value) => {
                      const provider = value as AgentSettings['cv_llm_provider'];
                      setDraftSettings({
                        ...draftSettings,
                        cv_llm_provider: provider,
                        cv_llm_model: (cvOptions?.models[provider] || CV_MODEL_FALLBACKS[provider])[0],
                      });
                    }}
                  >
                    <SelectTrigger id="cv-provider" className="h-10 w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {availableCvProviders.map((provider) => <SelectItem key={provider} value={provider}>{CV_PROVIDER_LABELS[provider]}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <label htmlFor="cv-model" className="text-sm font-medium">CV model</label>
                  <Select
                    value={draftSettings.cv_llm_model}
                    disabled={draftSettings.cv_use_llm === 'false'}
                    onValueChange={(value) => value && setDraftSettings({ ...draftSettings, cv_llm_model: value })}
                  >
                    <SelectTrigger id="cv-model" className="h-10 w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {availableCvModels.map((model) => <SelectItem key={model} value={model}>{model}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-2">
                <label htmlFor="cv-length" className="text-sm font-medium">Target length</label>
                <Select value={draftSettings.cv_length} onValueChange={(value) => setDraftSettings({ ...draftSettings, cv_length: value as AgentSettings['cv_length'] })}>
                  <SelectTrigger id="cv-length" className="h-10 w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="one_page">1 page</SelectItem>
                    <SelectItem value="one_half_page">1.5 pages</SelectItem>
                    <SelectItem value="two_page">2 pages</SelectItem>
                    <SelectItem value="auto">Auto (LLM)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-3 rounded-xl border border-white/10 bg-white/[0.025] p-3">
                <SettingSwitch
                  id="compile-pdf"
                  label="Compile PDF"
                  description="Required for automatic upload; uses Tectonic in the CV container."
                  checked={draftSettings.cv_compile_pdf === 'true'}
                  onCheckedChange={(checked) => setDraftSettings({ ...draftSettings, cv_compile_pdf: checked ? 'true' : 'false' })}
                />
                <Separator />
                <SettingSwitch
                  id="polish-llm"
                  label="Polish with LLM"
                  description="Rewrites the summary and bullets for the job description."
                  checked={draftSettings.cv_use_llm === 'true'}
                  onCheckedChange={(checked) => setDraftSettings({
                    ...draftSettings,
                    cv_use_llm: checked ? 'true' : 'false',
                    cv_enhance_tailor: checked ? draftSettings.cv_enhance_tailor : 'false',
                  })}
                />
                <Separator />
                <SettingSwitch
                  id="aggressive-tailor"
                  label="Aggressive tailor"
                  description="May add plausible JD-relevant details while preserving project, company, and role names."
                  checked={draftSettings.cv_enhance_tailor === 'true'}
                  disabled={draftSettings.cv_use_llm === 'false'}
                  onCheckedChange={(checked) => setDraftSettings({ ...draftSettings, cv_enhance_tailor: checked ? 'true' : 'false' })}
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="coverage-target" className="text-sm font-medium">Coverage target</label>
                <Select value={draftSettings.cv_coverage_target} disabled={draftSettings.cv_use_llm === 'false'} onValueChange={(value) => value && setDraftSettings({ ...draftSettings, cv_coverage_target: value })}>
                  <SelectTrigger id="coverage-target" className="h-10 w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">Off</SelectItem>
                    <SelectItem value="0.6">60%</SelectItem>
                    <SelectItem value="0.7">70%</SelectItem>
                    <SelectItem value="0.8">80%</SelectItem>
                    <SelectItem value="0.9">90%</SelectItem>
                    <SelectItem value="0.95">95%</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs leading-5 text-muted-foreground">Minimum JD keyword coverage the polishing loop aims to reach.</p>
              </div>
              <div className="space-y-2">
                <label htmlFor="cv-api" className="text-sm font-medium">Master CV service</label>
                <Input id="cv-api" value={draftSettings.cv_api_base} onChange={(event) => setDraftSettings({ ...draftSettings, cv_api_base: event.target.value })} />
              </div>
              <Separator />
              <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.05] px-3 py-3 text-sm">
                <div className="flex items-center gap-2 font-medium text-emerald-200"><ShieldCheck className="size-4" /> Stop before final Submit</div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">This safety rule is locked on and cannot be disabled.</p>
              </div>
            </div>
          )}
          <SheetFooter>
            <Button onClick={saveSettings} disabled={saving || !draftSettings}>
              {saving && <LoaderCircle className="size-4 animate-spin" />} Save settings
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Sheet open={memoryOpen} onOpenChange={setMemoryOpen}>
        <SheetContent className="border-white/10 bg-card sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Saved application answers</SheetTitle>
            <SheetDescription>ApplyPilot reuses these answers so it does not ask the same question again.</SheetDescription>
          </SheetHeader>
          <ScrollArea className="min-h-0 flex-1 px-4">
            <div className="space-y-2 pb-6 pr-3">
              {memory.map((fact) => (
                <div key={fact.memory_key} className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-xs text-muted-foreground">{fact.question || fact.memory_key}</div>
                      <div className="mt-1 break-words text-sm font-medium">{fact.answer}</div>
                      <div className="mt-1 text-[11px] text-muted-foreground">{fact.source === 'master_cv' ? 'From Master CV' : 'Learned from you'}</div>
                    </div>
                    <Button variant="ghost" size="icon-sm" aria-label={`Forget ${fact.question || fact.memory_key}`} onClick={() => removeMemory(fact.memory_key)}>
                      <Trash2 className="size-4 text-muted-foreground" />
                    </Button>
                  </div>
                </div>
              ))}
              {memory.length === 0 && <p className="py-12 text-center text-sm text-muted-foreground">No saved answers yet.</p>}
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </main>
  );
}

function SetupRow({ label, ready, detail }: { label: string; ready: boolean; detail: string }) {
  return (
    <div className="flex items-start gap-2.5">
      {ready ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-300" /> : <Circle className="mt-0.5 size-4 shrink-0 text-red-300" />}
      <div className="min-w-0 flex-1">
        <div className="font-medium">{label}</div>
        <div className="truncate text-xs text-muted-foreground">{detail}</div>
      </div>
    </div>
  );
}

function SettingSwitch({
  id,
  label,
  description,
  checked,
  disabled = false,
  onCheckedChange,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <label htmlFor={id} className={cn('min-w-0 cursor-pointer', disabled && 'cursor-not-allowed opacity-50')}>
        <span className="block text-sm font-medium">{label}</span>
        <span className="mt-1 block text-xs leading-5 text-muted-foreground">{description}</span>
      </label>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={onCheckedChange} className="mt-1" />
    </div>
  );
}
