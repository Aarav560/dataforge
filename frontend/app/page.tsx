"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthScreen } from "@/components/AuthScreen";
import { CommandBar } from "@/components/CommandBar";
import { StatsHUD } from "@/components/StatsHUD";
import { useWorkspaceLayout, WorkspaceLayout } from "@/components/Workspace";
import { api, UnauthorizedError, type Health, type Me, type ProjectSummary, type Team } from "@/lib/api";
import { useWorkspace, WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    api.me().then(setMe).catch((e) => setMe(e instanceof UnauthorizedError ? null : undefined));
    const onUnauthorized = () => setMe(null);
    window.addEventListener("dataforge:unauthorized", onUnauthorized);
    return () => window.removeEventListener("dataforge:unauthorized", onUnauthorized);
  }, []);

  if (me === null) return <AuthScreen onSignedIn={setMe} />;
  if (me === undefined) return <main className="flex min-h-dvh items-center justify-center text-sm muted">Connecting…</main>;
  return <SignedIn me={me} onSignOut={() => setMe(null)} />;
}

function SignedIn({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
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
      <Shell me={me} onSignOut={onSignOut} />
    </WorkspaceProvider>
  );
}

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const ws = useWorkspace();
  const layout = useWorkspaceLayout();
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [planning, setPlanning] = useState(false);
  const [goal, setGoal] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => ws.setError("Backend unreachable. Is it running on :8000?"));
    api.listProjects().then(setProjects).catch(() => {});
    api.teams().then(setTeams).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = async () => {
    await api.logout().catch(() => {});
    onSignOut();
  };

  const plan = async (text: string) => {
    setPlanning(true);
    ws.setError(null);
    try {
      const p = await api.createProject(text, teamId);
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
        {me.auth_enabled && (
          <span className="flex items-center gap-2 text-[11px]">
            <span className="mono muted" title={me.email}>{me.name || me.email}</span>
            <button className="btn" onClick={signOut}>Sign out</button>
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
            <div className="flex flex-wrap items-center gap-2">
              <button type="submit" className="btn btn-primary" disabled={planning || !goal.trim()}>
                {planning ? "Planning…" : "Plan"}
              </button>
              {teams.filter((t) => t.role !== "viewer").length > 0 && (
                <select className="input" value={teamId ?? ""} onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">private project</option>
                  {teams.filter((t) => t.role !== "viewer").map((t) => (
                    <option key={t.id} value={t.id}>share with {t.name}</option>
                  ))}
                </select>
              )}
            </div>
            <p className="text-[11px] muted">Tip: press Ctrl+K anywhere for the command bar.</p>
          </form>
        </section>
      )}
    </main>
  );
}
