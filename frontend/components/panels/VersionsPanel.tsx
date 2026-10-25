"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api,
  assetFileUrl,
  exportDownloadUrl,
  type ColumnStats,
  type Diff,
  type ExportCheck,
  type ExportJob,
  type Rows,
  type Version,
} from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { DataTable } from "../DataTable";

type Tab = "rows" | "stats" | "diff" | "card" | "export";

export function VersionsPanel() {
  const { versions, activeVersion, setActiveVersion } = useWorkspace();
  const [tab, setTab] = useState<Tab>("stats");
  const version = versions.find((v) => v.id === activeVersion) ?? versions.find((v) => v.status === "done");

  useEffect(() => {
    if (!activeVersion && version) setActiveVersion(version.id);
  }, [activeVersion, version, setActiveVersion]);

  if (versions.length === 0) return <div className="p-4 text-sm muted">No dataset versions yet. Run a pipeline.</div>;

  return (
    <div className="flex h-full min-h-0">
      <ul className="w-36 shrink-0 overflow-auto border-r border-[var(--border)] p-1 text-[11px] mono">
        {versions.map((v) => (
          <li key={v.id}>
            <button
              className={`w-full rounded px-2 py-1 text-left ${version?.id === v.id ? "bg-[var(--accent)]/15 text-[var(--accent)]" : "hover:bg-zinc-500/10"}`}
              onClick={() => setActiveVersion(v.id)}
            >
              <span className="font-semibold">v{v.version}</span>{" "}
              <span className={v.status === "done" ? "muted" : v.status === "error" ? "text-red-400" : "text-sky-400"}>
                {v.status === "done" ? `${v.row_count.toLocaleString()}r` : v.status}
              </span>
              {v.parent_version_id && <div className="muted">↳ from v{versions.find((p) => p.id === v.parent_version_id)?.version ?? "?"}</div>}
            </button>
          </li>
        ))}
      </ul>
      {version && (
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex gap-1 border-b border-[var(--border)] p-1">
            {(["stats", "rows", "diff", "card", "export"] as Tab[]).map((t) => (
              <button key={t} className={`btn ${tab === t ? "border-[var(--accent)] text-[var(--accent)]" : ""}`} onClick={() => setTab(t)}>
                {t}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {version.status === "error" ? (
              <pre className="whitespace-pre-wrap text-xs text-red-400">{version.error}</pre>
            ) : version.status !== "done" ? (
              <div className="text-xs muted">Running…</div>
            ) : tab === "stats" ? (
              <StatsView version={version} />
            ) : tab === "rows" ? (
              <RowsView version={version} />
            ) : tab === "diff" ? (
              <DiffView version={version} versions={versions} />
            ) : tab === "card" ? (
              <CardView version={version} />
            ) : (
              <ExportView version={version} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function RowsView({ version }: { version: Version }) {
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState<Rows | null>(null);
  const size = 50;
  useEffect(() => {
    api.rows(version.id, page * size, size).then(setRows).catch(() => setRows(null));
  }, [version.id, page]);
  if (!rows) return <div className="text-xs muted">Loading…</div>;
  const pages = Math.max(1, Math.ceil(rows.total / size));
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs">
        <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>‹</button>
        <span className="mono">page {page + 1} / {pages}</span>
        <button className="btn" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>›</button>
        <span className="muted">{rows.total.toLocaleString()} rows</span>
      </div>
      <DataTable rows={rows.rows} />
    </div>
  );
}

function StatsView({ version }: { version: Version }) {
  const cols = version.stats.columns ?? [];
  const hasImages = version.columns.some((c) => c.name === "asset_id") && version.columns.some((c) => c.name === "path");
  return (
    <div className="flex flex-col gap-3">
      {hasImages && <SampleGrid version={version} />}
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
        {cols.map((c) => (
          <ColumnCard key={c.name} col={c} rows={version.row_count} />
        ))}
      </div>
    </div>
  );
}

function ColumnCard({ col, rows }: { col: ColumnStats; rows: number }) {
  const nullPct = rows ? (100 * col.nulls) / rows : 0;
  return (
    <div className="panel p-2 text-[11px]">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate mono font-semibold" title={col.name}>{col.name}</span>
        <span className="muted mono">{col.type.toLowerCase()}</span>
      </div>
      <div className="mt-1 flex gap-3 muted">
        <span className={nullPct > 5 ? "text-amber-400" : ""}>{nullPct.toFixed(1)}% null</span>
        {col.distinct !== undefined && <span>~{col.distinct.toLocaleString()} distinct</span>}
      </div>
      {col.kind === "numeric" && col.min !== undefined && (
        <div className="mt-1 mono muted">
          min {fmt(col.min)} · mean {fmt(col.mean)} · max {fmt(col.max)}
        </div>
      )}
      {col.kind === "text" && col.avg_length !== undefined && <div className="mt-1 muted">avg length {Math.round(col.avg_length)}</div>}
      {col.histogram && <Histogram bins={col.histogram} />}
      {col.top && <TopValues top={col.top} total={rows} classLike={col.kind === "categorical" && col.type !== "BOOLEAN" && !NOT_CLASSES.has(col.name)} />}
    </div>
  );
}

function fmt(v: number | undefined): string {
  if (v === undefined || v === null) return "—";
  return Math.abs(v) >= 1000 || Number.isInteger(v) ? Math.round(v).toLocaleString() : v.toFixed(3);
}

function Histogram({ bins }: { bins: { from: number; to: number; count: number }[] }) {
  const max = Math.max(...bins.map((b) => b.count), 1);
  return (
    <div className="mt-2 flex h-12 items-end gap-px" aria-label="histogram">
      {bins.map((b, i) => (
        <div
          key={i}
          className="flex-1 rounded-t bg-[var(--accent)]/70"
          style={{ height: `${Math.max(2, (100 * b.count) / max)}%` }}
          title={`${fmt(b.from)} – ${fmt(b.to)}: ${b.count.toLocaleString()}`}
        />
      ))}
    </div>
  );
}

// Mirrors the backend: skew in these columns is expected, not a class imbalance.
const NOT_CLASSES = new Set(["split", "needs_review", "label_source", "label_confidence", "source_file", "chunk", "video", "game"]);

function TopValues({ top, total, classLike }: { top: { value: string; count: number }[]; total: number; classLike: boolean }) {
  const max = top[0]?.count || 1;
  const imbalanced = classLike && top.length >= 2 && top[top.length - 1].count > 0 && top[0].count / top[top.length - 1].count >= 5;
  return (
    <div className="mt-2 flex flex-col gap-0.5">
      {imbalanced && <div className="text-amber-400">imbalanced ({(top[0].count / top[top.length - 1].count).toFixed(1)}×)</div>}
      {top.slice(0, 8).map((t) => (
        <div key={t.value} className="flex items-center gap-1">
          <span className="w-20 truncate mono" title={t.value}>{t.value ?? "null"}</span>
          <div className="h-1.5 flex-1 rounded bg-zinc-500/20">
            <div className="h-1.5 rounded bg-sky-400" style={{ width: `${(100 * t.count) / max}%` }} />
          </div>
          <span className="w-12 text-right tabular-nums muted">{((100 * t.count) / Math.max(total, 1)).toFixed(0)}%</span>
        </div>
      ))}
    </div>
  );
}

function SampleGrid({ version }: { version: Version }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    api.rows(version.id, 0, 24).then((r) => setRows(r.rows)).catch(() => setRows([]));
  }, [version.id]);
  return (
    <div className="grid grid-cols-6 gap-1">
      {rows.map((r, i) => (
        <figure key={i} className="relative">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={assetFileUrl(Number(r.asset_id), String(r.path))} alt="" className="aspect-square w-full rounded object-cover" loading="lazy" />
          {r.label != null && (
            <figcaption className="absolute bottom-0 left-0 right-0 truncate bg-black/60 px-1 text-[10px] mono">{String(r.label)}</figcaption>
          )}
        </figure>
      ))}
    </div>
  );
}

function DiffView({ version, versions }: { version: Version; versions: Version[] }) {
  const others = versions.filter((v) => v.id !== version.id && v.status === "done" && v.version < version.version);
  const [against, setAgainst] = useState<number | undefined>(undefined);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDiff(null);
    setError(null);
    api.diff(version.id, against).then(setDiff).catch((e) => setError((e as Error).message));
  }, [version.id, against]);
  return (
    <div className="flex flex-col gap-2 text-xs">
      <label className="flex items-center gap-2">
        compare with
        <select className="input" value={against ?? ""} onChange={(e) => setAgainst(e.target.value ? Number(e.target.value) : undefined)}>
          <option value="">parent / previous</option>
          {others.map((v) => (
            <option key={v.id} value={v.id}>v{v.version}</option>
          ))}
        </select>
      </label>
      {error && <p className="muted">{error}</p>}
      {diff && (
        <>
          <div className="mono">
            v{diff.base.version} → v{diff.target.version}: {diff.rows.before.toLocaleString()} → {diff.rows.after.toLocaleString()} rows (
            <span className={diff.rows.delta >= 0 ? "text-green-500" : "text-red-400"}>{diff.rows.delta >= 0 ? "+" : ""}{diff.rows.delta.toLocaleString()}</span>)
          </div>
          {diff.rows.added !== undefined && (
            <div className="mono">
              <span className="text-green-500">+{diff.rows.added.toLocaleString()} added</span> ·{" "}
              <span className="text-red-400">−{diff.rows.removed?.toLocaleString()} removed</span> · {diff.rows.unchanged?.toLocaleString()} unchanged
            </div>
          )}
          {diff.columns.added.map((c) => <div key={c.name} className="text-green-500 mono">+ column {c.name} ({c.type})</div>)}
          {diff.columns.removed.map((c) => <div key={c.name} className="text-red-400 mono">− column {c.name} ({c.type})</div>)}
          {diff.columns.type_changed.map((c) => <div key={c.name} className="text-amber-400 mono">~ {c.name}: {c.before} → {c.after}</div>)}
          {diff.sample_added && diff.sample_added.length > 0 && (<><div className="muted">sample added</div><DataTable rows={diff.sample_added} /></>)}
          {diff.sample_removed && diff.sample_removed.length > 0 && (<><div className="muted">sample removed</div><DataTable rows={diff.sample_removed} /></>)}
        </>
      )}
    </div>
  );
}

function CardView({ version }: { version: Version }) {
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const md = (version.card_md ?? "").replace(/^---[\s\S]*?---\n/, "");
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true);
          try { await api.regenerateCard(version.id); await ws.refresh(["versions"]); } catch (e) { ws.setError((e as Error).message); } finally { setBusy(false); }
        }}>{busy ? "Writing…" : "Regenerate card"}</button>
        <button className="btn" onClick={() => navigator.clipboard?.writeText(version.card_md ?? "")}>Copy Markdown</button>
      </div>
      <article className="card-md text-[13px] leading-6">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </article>
    </div>
  );
}

function ExportView({ version }: { version: Version }) {
  const ws = useWorkspace();
  const [formats, setFormats] = useState<Record<string, string>>({});
  const [format, setFormat] = useState("parquet");
  const [push, setPush] = useState(false);
  const [repo, setRepo] = useState("");
  const [isPrivate, setPrivate] = useState(true);
  const [ack, setAck] = useState(false);
  const [check, setCheck] = useState<ExportCheck | null>(null);
  const [jobs, setJobs] = useState<ExportJob[]>([]);

  useEffect(() => {
    api.exportFormats().then(setFormats).catch(() => {});
  }, []);
  useEffect(() => {
    api.exportCheck(version.id, format, push, ack).then(setCheck).catch(() => setCheck(null));
  }, [version.id, format, push, ack]);
  useEffect(() => {
    api.exports(version.id).then(setJobs).catch(() => setJobs([]));
  }, [version.id, ws.exportTick]);
  useEffect(() => {
    if (format !== "hf") setPush(false);
  }, [format]);

  const start = async () => {
    try {
      await api.startExport(version.id, { format, push_to_hub: push, repo_id: push ? repo : undefined, private: isPrivate, acknowledge_unknown_licenses: ack });
      setJobs(await api.exports(version.id));
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  return (
    <div className="flex flex-col gap-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input" value={format} onChange={(e) => setFormat(e.target.value)}>
          {Object.keys(formats).map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {format === "hf" && (
          <label className="flex items-center gap-1">
            <input type="checkbox" className="accent-[var(--accent)]" checked={push} onChange={(e) => setPush(e.target.checked)} /> push to Hub
          </label>
        )}
        {push && (
          <>
            <input className="input mono" placeholder="user/dataset-name" value={repo} onChange={(e) => setRepo(e.target.value)} />
            <label className="flex items-center gap-1">
              <input type="checkbox" className="accent-[var(--accent)]" checked={isPrivate} onChange={(e) => setPrivate(e.target.checked)} /> private
            </label>
          </>
        )}
        <button className="btn btn-primary" disabled={!check?.allowed || (push && !/^[\w.-]+\/[\w.-]+$/.test(repo))} onClick={start}>
          Export
        </button>
      </div>
      {formats[format] && <p className="muted">{formats[format]}</p>}
      {check && (
        <div className="flex flex-col gap-1">
          {check.blockers.map((b) => <div key={b} className="text-red-400">✗ {b}</div>)}
          {check.warnings.map((w) => <div key={w} className="text-amber-400">! {w}</div>)}
          {check.needs_acknowledgement && (
            <label className="flex items-center gap-1">
              <input type="checkbox" className="accent-[var(--accent)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              I have checked that I may publish the sources with unknown terms
            </label>
          )}
          {check.allowed && check.warnings.length === 0 && <div className="text-green-500">✓ No license issues found.</div>}
        </div>
      )}
      {jobs.length > 0 && (
        <ul className="flex flex-col gap-1">
          {jobs.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-2 mono">
              <span>#{j.id} {j.format}</span>
              <span className={j.status === "done" ? "text-green-500" : j.status === "error" ? "text-red-400" : "text-sky-400"}>{j.status}</span>
              {j.status === "done" && <a className="text-sky-400 hover:underline" href={exportDownloadUrl(j.id)}>download zip ({j.files} files)</a>}
              {j.hub_url && <a className="text-sky-400 hover:underline" href={j.hub_url} target="_blank" rel="noreferrer">{j.hub_url}</a>}
              {j.error && <span className="text-red-400">{j.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
