"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, Asset, FeedEvent, Plan, Project, Result, Version, WS } from "./api";

export interface JobState { id: string; kind: string; status: string; progress: number; message: string; started: number }

export function useProject(pid: string | null) {
  const [project, setProject] = useState<Project | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [jobs, setJobs] = useState<Record<string, JobState>>({});
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({});
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [connected, setConnected] = useState(false);
  const [nodeStatus, setNodeStatus] = useState<Record<string, { status: string; note?: string }>>({});
  const pidRef = useRef(pid);
  pidRef.current = pid;

  const refreshResults = useCallback(async () => {
    if (!pidRef.current) return;
    const r = await api.results(pidRef.current);
    setResults(r);
    const c: Record<string, number> = {};
    r.forEach((x) => (c[x.source] = (c[x.source] ?? 0) + 1));
    setSourceCounts(c);
  }, []);
  const refreshAssets = useCallback(async () => { if (pidRef.current) setAssets(await api.assets(pidRef.current)); }, []);
  const refreshVersions = useCallback(async () => { if (pidRef.current) setVersions(await api.versions(pidRef.current)); }, []);
  const refreshProject = useCallback(async () => { if (pidRef.current) setProject(await api.project(pidRef.current)); }, []);

  useEffect(() => {
    if (!pid) return;
    setProject(null); setResults([]); setAssets([]); setVersions([]); setFeed([]); setJobs({}); setSourceCounts({});
    refreshProject(); refreshResults(); refreshAssets(); refreshVersions();

    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      ws = new WebSocket(`${WS}/ws/projects/${pid}`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); if (!closed) retry = setTimeout(connect, 2000); };
      ws.onmessage = (m) => {
        const e: FeedEvent = JSON.parse(m.data);
        if (e.type === "log" || e.type === "job" || e.type === "need") setFeed((f) => [...f.slice(-499), e]);
        if (e.replay) return;
        if (e.type === "results") {
          const incoming: Result[] = e.results;
          setResults((prev) => {
            const ids = new Set(prev.map((p) => p.id));
            return [...prev, ...incoming.filter((r) => !ids.has(r.id))].sort((a, b) => b.score - a.score);
          });
          setSourceCounts((c) => ({ ...c, [e.source]: (c[e.source] ?? 0) + incoming.length }));
          setFresh(new Set(incoming.map((r) => r.id)));
        } else if (e.type === "rescored") {
          refreshResults(); refreshProject();
        } else if (e.type === "need") {
          setProject((p) => p && ({ ...p, plan: { ...p.plan, needs: p.plan.needs.map((n) => n.id === e.need_id ? { ...n, status: e.status, count: e.count ?? n.count } : n) } }));
        } else if (e.type === "pipeline_node") {
          setNodeStatus((s) => ({ ...s, [e.node_id]: { status: e.status, note: e.note } }));
        } else if (e.type === "job") {
          setJobs((j) => ({ ...j, [e.job_id]: { id: e.job_id, kind: e.kind, status: e.status, progress: e.progress ?? j[e.job_id]?.progress ?? 0,
            message: e.message ?? "", started: j[e.job_id]?.started ?? e.ts } }));
          if (e.status === "done" || e.status === "error") {
            if (e.kind === "gather") refreshAssets();
            if (e.kind === "pipeline" || e.kind === "synthetic") { refreshVersions(); refreshAssets(); refreshResults(); }
          }
        }
      };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws?.close(); };
  }, [pid, refreshAssets, refreshProject, refreshResults, refreshVersions]);

  const updatePlan = useCallback(async (plan: Plan) => {
    if (!pidRef.current) return;
    setProject((p) => p && { ...p, plan });
    const saved = await api.savePlan(pidRef.current, plan);
    setProject((p) => p && { ...p, plan: saved });
  }, []);

  const toggleSelect = useCallback(async (r: Result) => {
    const sel = !r.selected;
    setResults((rs) => rs.map((x) => (x.id === r.id ? { ...x, selected: sel } : x)));
    await api.select(r.id, sel);
  }, []);

  return { project, results, assets, versions, feed, jobs, sourceCounts, fresh, connected, nodeStatus,
           refreshResults, refreshAssets, refreshVersions, refreshProject, updatePlan, toggleSelect, setFeed };
}
