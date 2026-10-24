"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityDrawer } from "@/components/CrawlFeed";
import { CommandAction, CommandBar } from "@/components/CommandBar";
import { DatasetWorkspace } from "@/components/DatasetWorkspace";
import { GatherView } from "@/components/GatherView";
import { Landing } from "@/components/Landing";
import { PipelineCanvas } from "@/components/PipelineCanvas";
import { PlanView } from "@/components/PlanTree";
import { PreviewPane } from "@/components/PreviewPane";
import { SearchView } from "@/components/ResultWall";
import { StatusBar } from "@/components/StatsHud";
import { SynthPanel } from "@/components/SynthPanel";
import { api, Connector, Project, Result } from "@/lib/api";
import { useProject } from "@/lib/useProject";

type Stage = "plan" | "search" | "gather" | "transform" | "synthesize" | "dataset";
const STAGES: { id: Stage; name: string; job?: string }[] = [
  { id: "plan", name: "Plan" },
  { id: "search", name: "Search", job: "search" },
  { id: "gather", name: "Gather", job: "gather" },
  { id: "transform", name: "Transform", job: "pipeline" },
  { id: "synthesize", name: "Synthesize", job: "synthetic" },
  { id: "dataset", name: "Dataset" },
];

export default function Home() {
  const [pid, setPid] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [health, setHealth] = useState<{ llm: { model: string | null; available: boolean }; connectors: Connector[] } | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [useLlm, setUseLlm] = useState(true);
  const [stage, setStage] = useState<Stage>("plan");
  const [focus, setFocus] = useState<Result | null>(null);
  const [llmJudge, setLlmJudge] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [error, setError] = useState("");
  const P = useProject(pid);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("p");
    if (p) { setPid(p); setStage("search"); }
    api.projects().then(setProjects).catch(() => setBackendDown(true));
    api.health().then(setHealth).catch(() => setBackendDown(true));
  }, []);

  const openProject = useCallback((id: string | null, to: Stage = "search") => {
    setPid(id); setFocus(null); setStage(to);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("p", id); else url.searchParams.delete("p");
    window.history.replaceState(null, "", url);
    if (!id) api.projects().then(setProjects).catch(() => {});
  }, []);

  const startGoal = useCallback(async (goal: string) => {
    setPlanning(true); setError("");
    try {
      const p = await api.createProject(goal, useLlm);
      setProjects((ps) => [p, ...ps]);
      openProject(p.id, "plan");
    } catch (e) { setError(`Couldn't plan that goal: ${e instanceof Error ? e.message : e}`); }
    setPlanning(false);
  }, [openProject, useLlm]);

  const runSearch = useCallback(async (needIds?: string[]) => {
    if (!pid) return;
    setError("");
    try { await api.search(pid, needIds, llmJudge); setStage("search"); } catch (e) { setError(`Search didn't start: ${e instanceof Error ? e.message : e}`); }
  }, [pid, llmJudge]);

  const runGather = useCallback(async () => {
    if (!pid) return;
    setError("");
    try { await api.gather(pid); setStage("gather"); } catch (e) { setError(`Download didn't start: ${e instanceof Error ? e.message : e}`); }
  }, [pid]);

  const selectedCount = P.results.filter((r) => r.selected).length;
  const running = Object.values(P.jobs).filter((j) => j.status === "running");
  const plan = P.project?.plan;

  const counts: Record<Stage, string | null> = {
    plan: plan ? String(plan.needs.filter((n) => n.enabled !== false).length) : null,
    search: P.results.length ? P.results.length.toLocaleString() : null,
    gather: selectedCount || P.assets.length ? `${selectedCount}` : null,
    transform: null,
    synthesize: plan?.synthetic.recommended ? "fit" : null,
    dataset: P.versions.length ? `v${P.versions[0].version}` : null,
  };

  const actions: CommandAction[] = useMemo(() => [
    { id: "search", label: "Search every source for this plan", group: "Actions", run: () => runSearch() },
    { id: "gather", label: `Download ${selectedCount} selected result${selectedCount === 1 ? "" : "s"}`, group: "Actions", run: runGather },
    { id: "judge", label: `${llmJudge ? "Turn off" : "Turn on"} local-LLM relevance check`, group: "Actions", run: () => setLlmJudge((x) => !x) },
    { id: "replan", label: "Plan this goal again", group: "Actions", run: async () => { if (pid) { await api.replan(pid, useLlm); P.refreshProject(); setStage("plan"); } } },
    ...STAGES.map((s, i) => ({ id: s.id, label: `Go to ${s.name}`, hint: `${i + 1}`, group: "Stages", run: () => setStage(s.id) })),
    { id: "activity", label: "Show activity log", group: "Stages", run: () => setActivityOpen(true) },
    { id: "home", label: "All projects", group: "Projects", run: () => openProject(null) },
    ...projects.slice(0, 8).map((p) => ({ id: `open-${p.id.slice(0, 4)}`, label: p.goal, group: "Projects", run: () => openProject(p.id) })),
  ], [runSearch, runGather, selectedCount, llmJudge, pid, useLlm, P, projects, openProject]);

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 bg-surface border-b border-line">
        <div className="h-14 flex items-center gap-3 px-4">
          <button onClick={() => openProject(null)} className="flex items-center gap-2 shrink-0" aria-label="DataForge — all projects">
            <Mark />
            <span className="display text-[17px] font-semibold">DataForge</span>
          </button>
          {pid && plan && (
            <>
              <span className="text-line-2 text-xl font-light" aria-hidden>/</span>
              <span className="truncate font-medium text-[14px] max-w-[34ch]" title={plan.goal}>{plan.goal}</span>
            </>
          )}
          <div className="flex-1" />
          <CommandBar actions={actions} onGoal={startGoal} busy={planning} />
          {pid && (
            <button className="btn btn-ghost" onClick={() => setActivityOpen((o) => !o)} aria-expanded={activityOpen}>
              {running.length ? <span className="ember" aria-hidden /> : null}
              Activity
            </button>
          )}
        </div>
        {pid && plan && (
          <nav className="flex items-end px-2 overflow-x-auto" aria-label="Stages">
            {STAGES.map((s, i) => {
              const job = running.find((j) => j.kind === s.job);
              return (
                <button key={s.id} className="stage" data-active={stage === s.id} data-hot={!!job} onClick={() => setStage(s.id)} aria-current={stage === s.id ? "step" : undefined}>
                  <span className="num">{String(i + 1).padStart(2, "0")}</span>
                  <span className="name">{s.name}</span>
                  {job ? <span className="count">{Math.round(job.progress * 100)}%</span> : counts[s.id] ? <span className="count">{counts[s.id]}</span> : null}
                  {job && <span className="heatbar" style={{ width: `calc((100% - 24px) * ${Math.max(0.04, job.progress)})` }} aria-hidden />}
                </button>
              );
            })}
          </nav>
        )}
      </header>

      {error && (
        <div role="alert" className="shrink-0 px-4 py-2 text-[13px] bg-bad-soft text-bad flex items-center gap-3 border-b border-line">
          {error}
          <button className="ml-auto btn btn-ghost btn-sm" onClick={() => setError("")}>Dismiss</button>
        </div>
      )}

      <main className="flex-1 min-h-0 relative">
        {!pid || !plan ? (
          <Landing projects={projects} onOpen={(id) => openProject(id)} onGoal={startGoal} planning={planning} useLlm={useLlm} setUseLlm={setUseLlm}
            health={health} backendDown={backendDown} loading={!!pid && !plan} />
        ) : (
          <div className="h-full flex">
            <div className="flex-1 min-w-0 h-full">
              {stage === "plan" && <PlanView plan={plan} onChange={P.updatePlan} onSearch={() => runSearch()} llmJudge={llmJudge} setLlmJudge={setLlmJudge} />}
              {stage === "search" && (
                <SearchView plan={plan} results={P.results} fresh={P.fresh} onToggle={P.toggleSelect} onPreview={setFocus} activeId={focus?.id ?? null}
                  onSearch={runSearch} searching={running.some((j) => j.kind === "search")} selectedCount={selectedCount} onReview={() => setStage("gather")} />
              )}
              {stage === "gather" && <GatherView pid={pid} results={P.results} assets={P.assets} onToggle={P.toggleSelect} onGather={runGather}
                gathering={running.some((j) => j.kind === "gather")} onNext={() => setStage("transform")} />}
              {stage === "transform" && <PipelineCanvas pid={pid} assets={P.assets} nodeStatus={P.nodeStatus} onRan={() => setActivityOpen(true)} />}
              {stage === "synthesize" && <div className="h-full overflow-auto"><SynthPanel pid={pid} plan={plan} onStarted={() => setActivityOpen(true)} /></div>}
              {stage === "dataset" && <DatasetWorkspace versions={P.versions} assets={P.assets} onChange={P.refreshVersions} />}
            </div>
            {focus && stage === "search" && (
              <aside className="drawer-in w-[400px] max-w-[90vw] shrink-0 border-l border-line bg-surface h-full overflow-auto absolute right-0 top-0 bottom-0 lg:static shadow-[var(--shadow-lg)] lg:shadow-none z-20">
                <PreviewPane result={focus} onClose={() => setFocus(null)} onToggle={P.toggleSelect}
                  selected={!!P.results.find((r) => r.id === focus.id)?.selected} />
              </aside>
            )}
          </div>
        )}
        {pid && activityOpen && <ActivityDrawer feed={P.feed} counts={P.sourceCounts} jobs={P.jobs} onClose={() => setActivityOpen(false)} />}
      </main>

      <StatusBar pid={pid} jobs={P.jobs} connected={P.connected} llm={health?.llm.model ?? null} />
    </div>
  );
}

function Mark() {
  // Anvil-meets-table glyph: a stack of rows on a forge base.
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
      <rect x="3" y="3" width="18" height="3.2" rx="1" fill="var(--ink)" />
      <rect x="3" y="8.2" width="18" height="3.2" rx="1" fill="var(--ink)" opacity=".55" />
      <rect x="3" y="13.4" width="18" height="3.2" rx="1" fill="var(--ink)" opacity=".3" />
      <path d="M6 19.5h12l-2 2H8z" fill="var(--heat)" />
    </svg>
  );
}
