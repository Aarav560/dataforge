"use client";

import { useCallback, useEffect, useState } from "react";
import { CommandBar } from "@/components/CommandBar";
import { StatsHUD } from "@/components/StatsHUD";
import { useWorkspaceLayout, WorkspaceLayout } from "@/components/Workspace";
import { api, type Health, type ProjectSummary } from "@/lib/api";
import { useWorkspace, WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  const [projectId, setProjectId] = useState<number | null>(null);

  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("project"));
    if (id) setProjectId(id);
  }, []);

  const change = useCallback((id: number | null) => {
    setProjectId(id);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("project", String(id));
    else url.searchParams.delete("project");
    window.history.replaceState(null, "", url);
  }, []);

  return (
    <WorkspaceProvider projectId={projectId} onProjectChange={change}>
      <Shell />
    </WorkspaceProvider>
  );
}

function Shell() {
  const ws = useWorkspace();
  const layout = useWorkspaceLayout();
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [planning, setPlanning] = useState(false);
  const [goal, setGoal] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => ws.setError("Backend unreachable. Is it running on :8000?"));
    api.listProjects().then(setProjects).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const plan = async (text: string) => {
    setPlanning(true);
    ws.setError(null);
    try {
      const p = await api.createProject(text);
      setProjects((ps) => [p, ...ps]);
      ws.openProject(p.id);
    } catch (e) {
      ws.setError((e as Error).message);
    } finally {
      setPlanning(false);
    }
  };

  return (
    <main className="flex h-dvh flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-[var(--border)] bg-[var(--panel)] px-3 py-2">
        <h1 className="mono text-lg font-bold tracking-tight text-[var(--accent)] [text-shadow:0_0_12px_var(--accent)]">DataForge</h1>
        <div className="order-last flex min-w-0 basis-full md:order-none md:basis-0 md:flex-1">
          <CommandBar projects={projects} onPlan={plan} onResetLayout={layout.reset} onShowPanel={layout.show} />
        </div>
        <select
          className="input max-w-[220px]"
          value={ws.project?.id ?? ""}
          onChange={(e) => ws.openProject(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Projects…</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id} {p.goal.slice(0, 40)}
            </option>
          ))}
        </select>
        {health && (
          <span className="hidden text-[11px] muted mono lg:inline">
            planner:{health.planner} · queue:{health.queue}
          </span>
        )}
      </header>

      {ws.error && (
        <div className="flex items-center justify-between border-b border-red-500/50 bg-red-500/10 px-3 py-1 text-sm text-red-400">
          <span className="break-words">{ws.error}</span>
          <button className="btn" onClick={() => ws.setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {ws.project ? (
        <>
          <WorkspaceLayout {...layout} />
          <StatsHUD />
        </>
      ) : (
        <section className="flex flex-1 items-center justify-center p-4">
          <form
            className="panel flex w-full max-w-xl flex-col gap-3 p-6"
            onSubmit={(e) => {
              e.preventDefault();
              if (goal.trim()) plan(goal.trim());
            }}
          >
            <h2 className="text-lg font-semibold">What do you want to train?</h2>
            <p className="text-sm muted">
              DataForge plans the data you need, searches Hugging Face, Kaggle, GitHub, YouTube and arXiv in parallel, ranks
              what it finds, and builds versioned datasets.
            </p>
            <input
              className="input py-2 text-sm"
              placeholder='e.g. "I want to train a Tetris AI"'
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              disabled={planning}
              autoFocus
            />
            <button type="submit" className="btn btn-primary self-start" disabled={planning || !goal.trim()}>
              {planning ? "Planning…" : "Plan"}
            </button>
            <p className="text-[11px] muted">Tip: press Ctrl+K anywhere for the command bar.</p>
          </form>
        </section>
      )}
    </main>
  );
}
