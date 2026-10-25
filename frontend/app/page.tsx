"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AssetPanel } from "@/components/AssetPanel";
import { DatasetsPanel } from "@/components/DatasetsPanel";
import { PlanTree } from "@/components/PlanTree";
import { ResultList } from "@/components/ResultList";
import { api, type Asset, type Health, type Project, type ProjectSummary, type Result } from "@/lib/api";

const POLL_MS = 1500;

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [goal, setGoal] = useState("");
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeNeed, setActiveNeed] = useState<number | null>(null);
  const [licenseFilter, setLicenseFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const projectId = project?.id;

  useEffect(() => {
    api.health().then(setHealth).catch(() => setError("Backend unreachable. Is it running on :8000?"));
    api.listProjects().then(setProjects).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    if (projectId === undefined) return;
    try {
      const [p, r, a] = await Promise.all([
        api.getProject(projectId),
        api.results(projectId, {
          need_id: activeNeed ?? undefined,
          license_class: licenseFilter,
          source: sourceFilter,
        }),
        api.assets(projectId),
      ]);
      setProject(p);
      setResults(r);
      setAssets(a);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, activeNeed, licenseFilter, sourceFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const busy =
    project?.needs.some((n) => n.status === "searching") ||
    assets.some((a) => a.status === "queued" || a.status === "downloading");

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [busy, refresh]);

  const downloaded = useMemo(() => new Map([...assets].reverse().map((a) => [a.result_id, a.status])), [assets]);

  const openProject = async (id: number) => {
    setActiveNeed(null);
    setSelected(new Set());
    try {
      setProject(await api.getProject(id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const createProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!goal.trim()) return;
    setPlanning(true);
    setError(null);
    try {
      const p = await api.createProject(goal.trim());
      setProjects((ps) => [p, ...ps]);
      setActiveNeed(null);
      setSelected(new Set());
      setProject(p);
      setGoal("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPlanning(false);
    }
  };

  const searchAll = async () => {
    if (!project) return;
    try {
      setProject(await api.search(project.id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const downloadSelected = async () => {
    if (!project || selected.size === 0) return;
    try {
      await api.download(project.id, [...selected]);
      setSelected(new Set());
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <main className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="mono text-lg font-bold text-[var(--accent)]">DataForge</h1>
        <form onSubmit={createProject} className="order-last flex min-w-0 basis-full gap-2 md:order-none md:basis-0 md:flex-1">
          <input
            className="input min-w-0 flex-1 py-2 text-sm"
            placeholder='Describe your ML goal, e.g. "I want to train a Tetris AI"'
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            disabled={planning}
          />
          <button type="submit" className="btn btn-primary" disabled={planning || !goal.trim()}>
            {planning ? "Planning…" : "Plan"}
          </button>
        </form>
        <select
          className="input max-w-[220px]"
          value={project?.id ?? ""}
          onChange={(e) => e.target.value && openProject(Number(e.target.value))}
        >
          <option value="">Projects…</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id} {p.goal.slice(0, 40)}
            </option>
          ))}
        </select>
        {health && (
          <span className="text-[11px] muted mono">
            planner:{health.planner} · queue:{health.queue}
          </span>
        )}
      </header>

      {error && (
        <div className="panel flex items-center justify-between border-red-500/50 p-2 text-sm text-red-400">
          <span>{error}</span>
          <button className="btn" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {!project ? (
        <section className="panel p-8 text-center">
          <p className="text-sm muted">
            Type a goal above. DataForge breaks it into data needs, searches Hugging Face, Kaggle and GitHub in
            parallel, ranks what it finds and downloads what you pick.
          </p>
        </section>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[300px_1fr_360px]">
          <aside className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Plan</h2>
              <button className="btn btn-primary" onClick={searchAll} disabled={busy && project.needs.every((n) => n.status === "searching")}>
                Search all
              </button>
            </div>
            <p className="text-xs muted">{project.goal}</p>
            <PlanTree
              project={project}
              activeNeed={activeNeed}
              onSelectNeed={setActiveNeed}
              onChange={refresh}
              onError={setError}
            />
          </aside>

          <section className="flex min-w-0 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">Results</h2>
              <span className="text-xs muted">{results.length}</span>
              <select className="input" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
                <option value="">all sources</option>
                <option value="huggingface">huggingface</option>
                <option value="kaggle">kaggle</option>
                <option value="github">github</option>
                <option value="youtube">youtube</option>
                <option value="arxiv">arxiv</option>
                <option value="semanticscholar">semanticscholar</option>
              </select>
              <select className="input" value={licenseFilter} onChange={(e) => setLicenseFilter(e.target.value)}>
                <option value="">any license</option>
                <option value="commercial">commercial OK</option>
                <option value="non_commercial">non-commercial</option>
                <option value="research_only">research only</option>
                <option value="local_only">local use only</option>
                <option value="unknown">unknown</option>
              </select>
              <button className="btn btn-primary ml-auto" disabled={selected.size === 0} onClick={downloadSelected}>
                Download {selected.size || ""}
              </button>
            </div>
            <ResultList results={results} selected={selected} downloaded={downloaded} onToggle={toggle} />
          </section>

          <aside className="flex flex-col gap-3">
            <AssetPanel assets={assets} />
            <DatasetsPanel projectId={project.id} assets={assets} onError={setError} />
            {health && !health.kaggle_downloads && (
              <p className="mt-2 text-[11px] muted">Kaggle downloads need KAGGLE_USERNAME and KAGGLE_KEY on the backend.</p>
            )}
          </aside>
        </div>
      )}
    </main>
  );
}
