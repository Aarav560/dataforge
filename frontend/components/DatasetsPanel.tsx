"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Advice, type Asset, type Pipeline, type Rows, type Template, type Version } from "@/lib/api";
import { DataTable } from "./DataTable";

const POLL_MS = 1500;

interface Props {
  projectId: number;
  assets: Asset[];
  onError: (msg: string) => void;
}

export function DatasetsPanel({ projectId, assets, onError }: Props) {
  const [advice, setAdvice] = useState<Advice | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [template, setTemplate] = useState("");
  const [assetIds, setAssetIds] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<{ version: Version; rows: Rows } | null>(null);
  const [showScaffold, setShowScaffold] = useState(false);

  const doneAssets = assets.filter((a) => a.status === "done");

  const refresh = useCallback(async () => {
    try {
      const [p, v] = await Promise.all([api.pipelines(projectId), api.versions(projectId)]);
      setPipelines(p);
      setVersions(v);
    } catch (e) {
      onError((e as Error).message);
    }
  }, [projectId, onError]);

  useEffect(() => {
    setPreview(null);
    api.templates().then(setTemplates).catch(() => {});
    api.advice(projectId).then(setAdvice).catch(() => {});
    refresh();
  }, [projectId, refresh]);

  const running = versions.some((v) => v.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [running, refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const chosen = templates.find((t) => t.id === template);

  const create = (tpl: string, ids: number[]) =>
    act(async () => {
      const p = await api.createPipeline(projectId, { template: tpl, asset_ids: ids });
      await api.runPipeline(p.id);
    });

  const openPreview = async (v: Version) => {
    try {
      setPreview({ version: v, rows: await api.rows(v.id, 0, 50) });
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {advice && (
        <div className={`panel p-3 ${advice.recommended ? "border-sky-500/50" : ""}`}>
          <div className="text-xs uppercase tracking-wide muted">Synthetic data advisor</div>
          <p className="mt-1 text-sm">
            {advice.recommended ? "Simulation recommended. " : "Collect real data. "}
            <span className="muted">{advice.reason}</span>
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {advice.template && (
              <button className="btn btn-primary" onClick={() => create(advice.template!, [])}>
                Generate with {advice.generator}
              </button>
            )}
            {advice.scaffold && (
              <button className="btn" onClick={() => setShowScaffold((s) => !s)}>
                {showScaffold ? "Hide" : "Show"} generator scaffold
              </button>
            )}
            <span className="self-center text-[11px] muted">via {advice.source}</span>
          </div>
          {showScaffold && advice.scaffold && (
            <pre className="mt-2 max-h-72 overflow-auto rounded bg-black/30 p-2 text-[11px] mono">{advice.scaffold}</pre>
          )}
        </div>
      )}

      <div className="panel p-3">
        <div className="text-xs uppercase tracking-wide muted">New pipeline</div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select className="input" value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">Choose a template…</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <button
            className="btn btn-primary"
            disabled={!chosen || (chosen.needs_assets && assetIds.size === 0)}
            onClick={() => create(template, [...assetIds])}
          >
            Create &amp; run
          </button>
        </div>
        {chosen && <p className="mt-1 text-xs muted">{chosen.description}</p>}
        {chosen?.needs_assets && (
          <div className="mt-2 flex flex-col gap-1">
            {doneAssets.length === 0 && <p className="text-xs muted">Download some results first.</p>}
            {doneAssets.map((a) => (
              <label key={a.id} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  className="accent-[var(--accent)]"
                  checked={assetIds.has(a.id)}
                  onChange={() =>
                    setAssetIds((s) => {
                      const n = new Set(s);
                      if (n.has(a.id)) n.delete(a.id);
                      else n.add(a.id);
                      return n;
                    })
                  }
                />
                <span className="mono">{a.title}</span>
              </label>
            ))}
          </div>
        )}
      </div>

      {pipelines.length > 0 && (
        <div className="panel p-3">
          <div className="text-xs uppercase tracking-wide muted">Pipelines</div>
          <ul className="mt-2 flex flex-col gap-1">
            {pipelines.map((p) => (
              <li key={p.id} className="flex items-center gap-2 text-xs">
                <span className="flex-1">
                  {p.name} <span className="muted mono">({p.graph_json.nodes.map((n) => n.type).join(" → ")})</span>
                </span>
                <button className="btn" onClick={() => act(() => api.runPipeline(p.id))}>
                  Run
                </button>
                <button className="btn" onClick={() => act(() => api.deletePipeline(p.id))}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="panel p-3">
        <div className="text-xs uppercase tracking-wide muted">Dataset versions</div>
        {versions.length === 0 ? (
          <p className="mt-1 text-xs muted">Run a pipeline to create a version.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-1">
            {versions.map((v) => (
              <li key={v.id} className="text-xs">
                <div className="flex items-center gap-2">
                  <span className="mono font-semibold">v{v.version}</span>
                  <span className={v.status === "done" ? "text-green-500" : v.status === "error" ? "text-red-400" : "text-sky-400"}>
                    {v.status}
                  </span>
                  {v.status === "done" && <span className="muted">{v.row_count.toLocaleString()} rows</span>}
                  {v.status === "done" && (
                    <button className="btn ml-auto" onClick={() => openPreview(v)}>
                      Preview
                    </button>
                  )}
                </div>
                {v.error && <div className="text-red-400 break-words">{v.error}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {preview && (
        <div className="panel p-3">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span>
              v{preview.version.version} · showing {preview.rows.rows.length} of {preview.rows.total.toLocaleString()} rows
            </span>
            <button className="btn" onClick={() => setPreview(null)}>
              Close
            </button>
          </div>
          <DataTable rows={preview.rows.rows} />
          {preview.version.log && (
            <details className="mt-2 text-[11px]">
              <summary className="muted">Run log</summary>
              <pre className="whitespace-pre-wrap mono">{preview.version.log}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
