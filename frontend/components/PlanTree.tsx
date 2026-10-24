"use client";

import { useState } from "react";
import { api, sourceQueries, type Kind, type Need, type Project } from "@/lib/api";

const KINDS: Kind[] = ["dataset", "model", "environment", "code"];

const STATUS_DOT: Record<Need["status"], string> = {
  pending: "bg-zinc-500",
  searching: "bg-sky-400 animate-pulse",
  done: "bg-green-500",
  error: "bg-red-500",
};

interface Props {
  project: Project;
  activeNeed: number | null;
  onSelectNeed: (id: number | null) => void;
  onChange: () => void;
  onError: (msg: string) => void;
}

export function PlanTree({ project, activeNeed, onSelectNeed, onChange, onError }: Props) {
  const [adding, setAdding] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChange();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="panel p-3">
        <div className="text-xs uppercase tracking-wide muted">Approach</div>
        <div className="mono text-sm">{project.approach ?? "—"}</div>
        {project.approach_reason && <p className="mt-1 text-xs muted">{project.approach_reason}</p>}
        <div className="mt-1 text-[11px] muted">planner: {project.planner}</div>
      </div>

      <button
        className={`text-left text-xs px-1 ${activeNeed === null ? "text-[var(--accent)]" : "muted"}`}
        onClick={() => onSelectNeed(null)}
      >
        All needs
      </button>

      {project.needs.map((need) => (
        <NeedNode
          key={need.id}
          need={need}
          active={activeNeed === need.id}
          onSelect={() => onSelectNeed(need.id)}
          onSave={(patch) => run(() => api.updateNeed(need.id, patch))}
          onDelete={() => run(() => api.deleteNeed(need.id))}
          onSearch={() => run(() => api.search(project.id, [need.id]))}
        />
      ))}

      {adding ? (
        <NeedEditor
          initial={{ description: "", kind: "dataset", queries: [] }}
          onCancel={() => setAdding(false)}
          onSave={(n) =>
            run(async () => {
              await api.addNeed(project.id, n);
              setAdding(false);
            })
          }
        />
      ) : (
        <button className="btn self-start" onClick={() => setAdding(true)}>
          + Add need
        </button>
      )}
    </div>
  );
}

function NeedNode(props: {
  need: Need;
  active: boolean;
  onSelect: () => void;
  onSave: (patch: { description: string; kind: Kind; queries: string[] }) => void;
  onDelete: () => void;
  onSearch: () => void;
}) {
  const { need, active } = props;
  const [editing, setEditing] = useState(false);
  const queries = sourceQueries(need);

  if (editing) {
    return (
      <NeedEditor
        initial={{ description: need.description, kind: need.kind, queries }}
        onCancel={() => setEditing(false)}
        onSave={(n) => {
          props.onSave(n);
          setEditing(false);
        }}
      />
    );
  }

  return (
    <div
      className={`panel p-2 cursor-pointer ${active ? "border-[var(--accent)]" : ""}`}
      onClick={props.onSelect}
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[need.status]}`} title={need.status} />
        <div className="min-w-0 flex-1">
          <div className="text-sm">{need.description}</div>
          <div className="mt-0.5 flex flex-wrap gap-1 text-[11px]">
            <span className="mono rounded bg-zinc-500/20 px-1">{need.kind}</span>
            {queries.map((q) => (
              <span key={q} className="mono muted">
                “{q}”
              </span>
            ))}
          </div>
          <div className="mt-0.5 text-[11px] muted">
            {Object.keys(need.queries).join(" · ")} — {need.result_count} results
          </div>
          {need.error && (
            <details className="mt-1 text-[11px] text-amber-500" onClick={(e) => e.stopPropagation()}>
              <summary>{need.status === "error" ? "search failed" : "some sources failed"}</summary>
              <pre className="whitespace-pre-wrap mono">{need.error}</pre>
            </details>
          )}
        </div>
      </div>
      <div className="mt-1 flex gap-1" onClick={(e) => e.stopPropagation()}>
        <button className="btn" disabled={need.status === "searching"} onClick={props.onSearch}>
          Search
        </button>
        <button className="btn" disabled={need.status === "searching"} onClick={() => setEditing(true)}>
          Edit
        </button>
        <button className="btn" onClick={props.onDelete}>
          Remove
        </button>
      </div>
    </div>
  );
}

function NeedEditor(props: {
  initial: { description: string; kind: Kind; queries: string[] };
  onSave: (n: { description: string; kind: Kind; queries: string[] }) => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(props.initial.description);
  const [kind, setKind] = useState<Kind>(props.initial.kind);
  const [queries, setQueries] = useState(props.initial.queries.join("\n"));
  const parsed = queries.split("\n").map((q) => q.trim()).filter(Boolean);
  const valid = description.trim() && parsed.length > 0;

  return (
    <form
      className="panel flex flex-col gap-2 p-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) props.onSave({ description: description.trim(), kind, queries: parsed });
      }}
    >
      <input
        className="input"
        placeholder="What data is needed?"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        autoFocus
      />
      <select className="input" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
        {KINDS.map((k) => (
          <option key={k} value={k}>
            {k}
          </option>
        ))}
      </select>
      <textarea
        className="input mono"
        rows={3}
        placeholder="One search query per line"
        value={queries}
        onChange={(e) => setQueries(e.target.value)}
      />
      <div className="flex gap-1">
        <button type="submit" className="btn btn-primary" disabled={!valid}>
          Save
        </button>
        <button type="button" className="btn" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
