"use client";

import { formatBytes } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export function StatsHUD() {
  const { stats, connected, versions } = useWorkspace();
  if (!stats) return null;
  const quota = Object.entries(stats.quota);
  // ETA from bytes remaining across downloads with a known size.
  const remaining = stats.downloads.reduce((acc, d) => acc + (d.size ? Math.max(d.size - d.bytes, 0) : 0), 0);
  const running = versions.filter((v) => v.status === "running").length;
  return (
    <footer className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-[var(--border)] bg-[var(--panel)] px-3 py-1 text-[11px] mono">
      <Stat label="rows" value={stats.rows.toLocaleString()} hint={stats.latest_version ? `v${stats.latest_version}` : undefined} />
      <Stat label="results" value={stats.results.toLocaleString()} />
      <Stat label="gathered" value={formatBytes(stats.bytes_gathered)} hint={`${stats.assets_done} assets`} />
      <Stat label="workers" value={String(stats.workers_active)} live={stats.workers_active > 0} />
      {stats.downloads.length > 0 && <Stat label="downloading" value={`${stats.downloads.length}`} hint={remaining ? `${formatBytes(remaining)} left` : undefined} />}
      {running > 0 && <Stat label="pipelines" value={String(running)} live />}
      {quota.map(([host, q]) => (
        <Stat
          key={host}
          label={host.replace(/^api\./, "").replace(/\.(com|org|co)$/, "")}
          value={`${q.remaining}${q.limit ? `/${q.limit}` : ""}`}
          warn={q.limit ? q.remaining / q.limit < 0.1 : q.remaining < 5}
        />
      ))}
      <span className={`ml-auto ${connected ? "text-[var(--accent)]" : "text-amber-400"}`}>{connected ? "● stream" : "○ polling"}</span>
    </footer>
  );
}

function Stat({ label, value, hint, live, warn }: { label: string; value: string; hint?: string; live?: boolean; warn?: boolean }) {
  return (
    <span>
      <span className="muted">{label} </span>
      <span className={`tabular-nums ${warn ? "text-red-400" : live ? "text-[var(--accent)] animate-pulse" : ""}`}>{value}</span>
      {hint && <span className="muted"> ({hint})</span>}
    </span>
  );
}
