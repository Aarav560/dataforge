"use client";

import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { api, type ProjectSummary, type Template } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

interface Props {
  projects: ProjectSummary[];
  onPlan: (goal: string) => Promise<void>;
  onResetLayout: () => void;
  onShowPanel: (id: string) => void;
}

export function CommandBar({ projects, onPlan, onResetLayout, onShowPanel }: Props) {
  const ws = useWorkspace();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [templates, setTemplates] = useState<Template[]>([]);

  useEffect(() => {
    api.templates().then(setTemplates).catch(() => {});
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const run = async (fn: () => Promise<unknown> | unknown) => {
    setOpen(false);
    setQuery("");
    try {
      await fn();
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  const project = ws.project;
  const goal = query.trim();
  const isCommand = goal.startsWith("/");

  return (
    <>
      <button
        className="input flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm muted"
        onClick={() => setOpen(true)}
        aria-label="Open command bar"
      >
        <span className="mono text-[var(--accent)]">›</span>
        <span className="truncate">Type a goal or a command (/search, /pipeline, /download)…</span>
        <kbd className="ml-auto rounded border border-[var(--border)] px-1 text-[10px] mono">Ctrl K</kbd>
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-[12vh]" onClick={() => setOpen(false)}>
          <Command
            className="panel w-full max-w-xl overflow-hidden shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            loop
            shouldFilter={!(goal && !isCommand)}
          >
            <Command.Input
              autoFocus
              value={query}
              onValueChange={setQuery}
              placeholder="Describe a goal, or type / for commands"
              className="w-full border-b border-[var(--border)] bg-transparent px-3 py-3 text-sm outline-none mono"
            />
            <Command.List className="max-h-[50vh] overflow-auto p-1 text-sm">
              <Command.Empty className="p-3 text-xs muted">No matching command.</Command.Empty>
              {goal && !isCommand && (
                <Command.Group heading="Plan">
                  <Item onSelect={() => run(() => onPlan(goal))}>
                    Plan a dataset for “{goal}”
                  </Item>
                </Command.Group>
              )}
              {project && (
                <Command.Group heading="Search">
                  <Item value="/search all" onSelect={() => run(async () => { await api.search(project.id); ws.refresh(["project"]); })}>
                    /search <span className="muted">all needs</span>
                  </Item>
                  {project.needs.map((n) => (
                    <Item key={n.id} value={`/search ${n.description}`} onSelect={() => run(async () => { await api.search(project.id, [n.id]); ws.refresh(["project"]); })}>
                      /search <span className="muted">{n.description}</span>
                    </Item>
                  ))}
                </Command.Group>
              )}
              {project && ws.selected.size > 0 && (
                <Command.Group heading="Gather">
                  <Item value="/download selected" onSelect={() => run(async () => { await api.download(project.id, [...ws.selected]); ws.clearSelected(); ws.refresh(["assets"]); })}>
                    /download <span className="muted">{ws.selected.size} selected</span>
                  </Item>
                </Command.Group>
              )}
              {project && (
                <Command.Group heading="Pipeline">
                  {templates.filter((t) => !t.needs_assets).map((t) => (
                    <Item key={t.id} value={`/pipeline run ${t.label}`} onSelect={() => run(async () => {
                      const p = await api.createPipeline(project.id, { template: t.id });
                      await api.runPipeline(p.id);
                      ws.refresh(["pipelines", "versions"]);
                    })}>
                      /pipeline <span className="muted">run {t.label}</span>
                    </Item>
                  ))}
                  <Item value="/pipeline open canvas" onSelect={() => run(() => onShowPanel("canvas"))}>
                    /pipeline <span className="muted">open canvas</span>
                  </Item>
                </Command.Group>
              )}
              <Command.Group heading="Index">
                <Item value="/index sync" onSelect={() => run(async () => { await api.syncIndex(); })}>
                  /index <span className="muted">sync hub metadata now</span>
                </Item>
              </Command.Group>
              {ws.versions.some((v) => v.status === "done") && (
                <Command.Group heading="Export">
                  <Item value="/export open" onSelect={() => run(() => onShowPanel("versions"))}>
                    /export <span className="muted">open dataset workspace</span>
                  </Item>
                </Command.Group>
              )}
              <Command.Group heading="View">
                {["plan", "feed", "graph", "results", "canvas", "preview", "licenses", "datasets", "versions", "review"].map((id) => (
                  <Item key={id} value={`/show ${id}`} onSelect={() => run(() => onShowPanel(id))}>
                    /show <span className="muted">{id}</span>
                  </Item>
                ))}
                <Item value="/layout reset" onSelect={() => run(onResetLayout)}>
                  /layout <span className="muted">reset panels</span>
                </Item>
              </Command.Group>
              {projects.length > 0 && (
                <Command.Group heading="Projects">
                  {projects.slice(0, 20).map((p) => (
                    <Item key={p.id} value={`/open ${p.id} ${p.goal}`} onSelect={() => run(() => ws.openProject(p.id))}>
                      /open <span className="muted">#{p.id} {p.goal}</span>
                    </Item>
                  ))}
                </Command.Group>
              )}
            </Command.List>
          </Command>
        </div>
      )}
    </>
  );
}

function Item({ children, onSelect, value }: { children: React.ReactNode; onSelect: () => void; value?: string }) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="cursor-pointer rounded px-3 py-2 mono text-[13px] data-[selected=true]:bg-[var(--accent)]/15 data-[selected=true]:text-[var(--accent)]"
    >
      {children}
    </Command.Item>
  );
}
