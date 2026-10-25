"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type Asset,
  type LiveEvent,
  type Pipeline,
  type Project,
  type Result,
  type Stats,
  type Version,
} from "./api";
import { useEvents } from "./useEvents";

export interface FeedLine {
  id: number;
  ts: number;
  source: string;
  text: string;
  tone: "info" | "ok" | "warn" | "error";
}

export interface Filters {
  needId: number | null;
  license: string;
  source: string;
}

interface Workspace {
  project: Project | null;
  results: Result[];
  assets: Asset[];
  versions: Version[];
  pipelines: Pipeline[];
  stats: Stats | null;
  feed: FeedLine[];
  sourceHits: Record<string, number>;
  freshResults: Set<number>;
  stepStates: Record<string, string>;
  connected: boolean;
  filters: Filters;
  setFilters: (f: Partial<Filters>) => void;
  selected: Set<number>;
  toggleSelected: (id: number) => void;
  clearSelected: () => void;
  focused: number | null;
  setFocused: (id: number | null) => void;
  error: string | null;
  setError: (msg: string | null) => void;
  /** Bumps when an export finishes, so export lists refetch. */
  exportTick: number;
  activeVersion: number | null;
  setActiveVersion: (id: number | null) => void;
  refresh: (what?: ("project" | "results" | "assets" | "versions" | "pipelines" | "stats")[]) => Promise<void>;
  openProject: (id: number | null) => void;
}

const Ctx = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const ws = useContext(Ctx);
  if (!ws) throw new Error("useWorkspace outside WorkspaceProvider");
  return ws;
}

const ALL = ["project", "results", "assets", "versions", "pipelines", "stats"] as const;
let feedId = 0;

function describe(e: LiveEvent): Omit<FeedLine, "id" | "ts"> | null {
  const s = (k: string) => String(e[k] ?? "");
  switch (e.type) {
    case "need_started":
      return { source: "plan", text: `searching "${s("description")}" across ${(e.sources as string[]).join(", ")}`, tone: "info" };
    case "hits":
      return {
        source: s("source"),
        text: `${s("count")} hits${e.query ? ` for "${s("query")}"` : ""} · ${s("new")} new${e.ms ? ` · ${s("ms")}ms` : ""}${
          e.origin && e.origin !== "live" ? ` · from ${s("origin")}` : ""
        }`,
        tone: Number(e.count) > 0 ? "ok" : "info",
      };
    case "source_error":
      return { source: s("source"), text: `failed "${s("query")}": ${s("error")}`, tone: "error" };
    case "need_done":
      return { source: "plan", text: `need done: ${s("results")} results, ${s("errors")} errors`, tone: e.status === "error" ? "error" : "ok" };
    case "download_started":
      return { source: s("source"), text: `downloading ${s("title")}`, tone: "info" };
    case "download_done":
      return { source: "gather", text: `downloaded ${s("title")} (${(Number(e.bytes) / 1e6).toFixed(1)} MB)`, tone: "ok" };
    case "download_error":
      return { source: "gather", text: `download failed: ${s("error")}`, tone: "error" };
    case "version_started":
      return { source: "pipeline", text: `run started (version #${s("version_id")})`, tone: "info" };
    case "pipeline_log":
      return { source: "pipeline", text: s("message"), tone: "info" };
    case "version_done":
      return { source: "pipeline", text: `version ready: ${Number(e.rows).toLocaleString()} rows`, tone: "ok" };
    case "version_error":
      return { source: "pipeline", text: `run failed: ${s("error")}`, tone: "error" };
    case "export_started":
      return { source: "export", text: `exporting ${s("format")}…`, tone: "info" };
    case "export_done":
      return { source: "export", text: `${s("format")} export ready (${s("files")} files)${e.hub_url ? ` · pushed to ${s("hub_url")}` : ""}`, tone: "ok" };
    case "export_error":
      return { source: "export", text: `export failed: ${s("error")}`, tone: "error" };
    default:
      return null;
  }
}

export function WorkspaceProvider({ projectId, onProjectChange, children }: {
  projectId: number | null;
  onProjectChange: (id: number | null) => void;
  children: React.ReactNode;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [feed, setFeed] = useState<FeedLine[]>([]);
  const [sourceHits, setSourceHits] = useState<Record<string, number>>({});
  const [freshResults, setFresh] = useState<Set<number>>(new Set());
  const [stepStates, setStepStates] = useState<Record<string, string>>({});
  const [filters, setFiltersState] = useState<Filters>({ needId: null, license: "", source: "" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [focused, setFocused] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportTick, setExportTick] = useState(0);
  const [activeVersion, setActiveVersion] = useState<number | null>(null);

  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const refresh = useCallback(
    async (what: readonly (typeof ALL)[number][] = ALL) => {
      if (projectId === null) return;
      const f = filtersRef.current;
      try {
        await Promise.all(
          what.map(async (w) => {
            if (w === "project") setProject(await api.getProject(projectId));
            if (w === "results")
              setResults(
                await api.results(projectId, { need_id: f.needId ?? undefined, license_class: f.license, source: f.source, limit: 500 }),
              );
            if (w === "assets") setAssets(await api.assets(projectId));
            if (w === "versions") setVersions(await api.versions(projectId));
            if (w === "pipelines") setPipelines(await api.pipelines(projectId));
            if (w === "stats") setStats(await api.stats(projectId));
          }),
        );
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [projectId],
  );

  useEffect(() => {
    setProject(null);
    setResults([]);
    setAssets([]);
    setVersions([]);
    setPipelines([]);
    setStats(null);
    setFeed([]);
    setSourceHits({});
    setSelected(new Set());
    setFocused(null);
    setActiveVersion(null);
    setFiltersState({ needId: null, license: "", source: "" });
    refresh();
  }, [projectId, refresh]);

  useEffect(() => {
    refresh(["results"]);
  }, [filters, refresh]);

  // Coalesce bursts of events into one refetch per resource.
  const pending = useRef(new Set<(typeof ALL)[number]>());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const schedule = useCallback(
    (...what: (typeof ALL)[number][]) => {
      what.forEach((w) => pending.current.add(w));
      clearTimeout(flushTimer.current);
      flushTimer.current = setTimeout(() => {
        const todo = [...pending.current];
        pending.current.clear();
        refresh(todo);
      }, 250);
    },
    [refresh],
  );

  const onEvent = useCallback(
    (e: LiveEvent) => {
      const line = describe(e);
      if (line) setFeed((f) => [...f.slice(-499), { ...line, id: ++feedId, ts: e.ts }]);
      if (e.replay) return;
      switch (e.type) {
        case "hits": {
          const ids = (e.result_ids as number[]) ?? [];
          setSourceHits((h) => ({ ...h, [String(e.source)]: (h[String(e.source)] ?? 0) + Number(e.count) }));
          setFresh((s) => new Set([...s, ...ids]));
          setTimeout(() => setFresh((s) => new Set([...s].filter((id) => !ids.includes(id)))), 2500);
          schedule("results", "stats");
          break;
        }
        case "need_started":
        case "need_done":
          schedule("project", "results", "stats");
          break;
        case "download_started":
        case "download_done":
        case "download_error":
        case "download_progress":
          schedule("assets", "stats");
          break;
        case "pipeline_step":
          setStepStates((s) => ({ ...s, [String(e.node_id)]: String(e.state) }));
          break;
        case "version_started":
          setStepStates({});
          schedule("versions", "stats");
          break;
        case "version_done":
        case "version_error":
          schedule("versions", "stats");
          break;
        case "export_done":
        case "export_error":
          setExportTick((t) => t + 1);
          break;
      }
    },
    [schedule],
  );

  const connected = useEvents(projectId ?? undefined, onEvent);

  // Fallback polling while work is in flight and the socket is down.
  const busy =
    !!project?.needs.some((n) => n.status === "searching") ||
    assets.some((a) => a.status === "queued" || a.status === "downloading") ||
    versions.some((v) => v.status === "running");
  useEffect(() => {
    if (connected || !busy) return;
    const t = setInterval(() => refresh(), 2000);
    return () => clearInterval(t);
  }, [connected, busy, refresh]);

  const value = useMemo<Workspace>(
    () => ({
      project, results, assets, versions, pipelines, stats, feed, sourceHits, freshResults, stepStates, connected,
      filters,
      setFilters: (f) => setFiltersState((cur) => ({ ...cur, ...f })),
      selected,
      toggleSelected: (id) =>
        setSelected((s) => {
          const n = new Set(s);
          if (n.has(id)) n.delete(id);
          else n.add(id);
          return n;
        }),
      clearSelected: () => setSelected(new Set()),
      focused, setFocused, error, setError, exportTick, activeVersion, setActiveVersion,
      refresh: (what) => refresh(what ?? ALL),
      openProject: onProjectChange,
    }),
    [project, results, assets, versions, pipelines, stats, feed, sourceHits, freshResults, stepStates, connected,
     filters, selected, focused, error, exportTick, activeVersion, refresh, onProjectChange],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
