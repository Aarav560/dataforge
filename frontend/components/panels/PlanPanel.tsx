"use client";

import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { PlanTree } from "../PlanTree";

export function PlanPanel() {
  const ws = useWorkspace();
  const { project, filters, setFilters } = ws;
  if (!project) return null;
  const busy = project.needs.some((n) => n.status === "searching");

  const searchAll = async () => {
    try {
      await api.search(project.id);
      ws.refresh(["project", "stats"]);
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col gap-2 overflow-auto p-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs muted">{project.goal}</p>
        <button className="btn btn-primary shrink-0" onClick={searchAll} disabled={busy}>
          {busy ? "Searching…" : "Search all"}
        </button>
      </div>
      <PlanTree
        project={project}
        activeNeed={filters.needId}
        onSelectNeed={(id) => setFilters({ needId: id })}
        onChange={() => ws.refresh(["project", "results", "stats"])}
        onError={ws.setError}
      />
    </div>
  );
}
