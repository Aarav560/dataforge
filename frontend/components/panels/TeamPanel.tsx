"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Team } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

const ROLES = ["viewer", "editor", "owner"] as const;

export function TeamPanel() {
  const ws = useWorkspace();
  const [teams, setTeams] = useState<Team[]>([]);
  const [newTeam, setNewTeam] = useState("");
  const [invite, setInvite] = useState<Record<number, { email: string; role: string }>>({});

  const load = useCallback(() => api.teams().then(setTeams).catch((e) => ws.setError((e as Error).message)), [ws]);
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  const project = ws.project;
  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-2 text-xs">
      {project && (
        <div className="panel p-2">
          <div className="uppercase tracking-wide muted">This project</div>
          <label className="mt-1 flex items-center gap-2">
            shared with
            <select
              className="input"
              value={project.team_id ?? ""}
              onChange={(e) =>
                act(async () => {
                  await api.shareProject(project.id, e.target.value ? Number(e.target.value) : null);
                  await ws.refresh(["project"]);
                })
              }
            >
              <option value="">only me</option>
              {teams.filter((t) => t.role !== "viewer").map((t) => (
                <option key={t.id} value={t.id}>
                  team {t.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (newTeam.trim()) act(async () => { await api.createTeam(newTeam.trim()); setNewTeam(""); });
        }}
      >
        <input className="input flex-1" placeholder="New team name" value={newTeam} onChange={(e) => setNewTeam(e.target.value)} />
        <button className="btn btn-primary" disabled={!newTeam.trim()}>Create team</button>
      </form>

      {teams.length === 0 && <p className="muted">No teams yet. Create one to share projects.</p>}
      {teams.map((t) => {
        const owner = t.role === "owner";
        const inv = invite[t.id] ?? { email: "", role: "editor" };
        return (
          <div key={t.id} className="panel p-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{t.name}</span>
              <span className="mono muted">you: {t.role}</span>
            </div>
            <ul className="mt-2 flex flex-col gap-1">
              {t.members.map((m) => (
                <li key={m.user_id} className="flex items-center gap-2">
                  <span className="flex-1 truncate mono">{m.email}</span>
                  {owner ? (
                    <select className="input" value={m.role} onChange={(e) => act(() => api.changeRole(t.id, m.user_id, e.target.value))}>
                      {ROLES.map((r) => <option key={r}>{r}</option>)}
                    </select>
                  ) : (
                    <span className="muted">{m.role}</span>
                  )}
                  {owner && (
                    <button className="btn" onClick={() => act(() => api.removeMember(t.id, m.user_id))} aria-label={`Remove ${m.email}`}>
                      ✕
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {owner && (
              <form
                className="mt-2 flex gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(async () => {
                    await api.addMember(t.id, inv.email, inv.role);
                    setInvite((s) => ({ ...s, [t.id]: { email: "", role: "editor" } }));
                  });
                }}
              >
                <input className="input min-w-0 flex-1" type="email" placeholder="member@email" value={inv.email}
                  onChange={(e) => setInvite((s) => ({ ...s, [t.id]: { ...inv, email: e.target.value } }))} />
                <select className="input" value={inv.role} onChange={(e) => setInvite((s) => ({ ...s, [t.id]: { ...inv, role: e.target.value } }))}>
                  {ROLES.map((r) => <option key={r}>{r}</option>)}
                </select>
                <button className="btn" disabled={!inv.email}>Add</button>
              </form>
            )}
          </div>
        );
      })}
    </div>
  );
}
