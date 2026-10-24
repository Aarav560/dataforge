export type Kind = "dataset" | "model" | "environment" | "code";
export type LicenseClass = "commercial" | "non_commercial" | "research_only" | "unknown";

export interface Need {
  id: number;
  description: string;
  kind: Kind;
  queries: Record<string, string[]>;
  status: "pending" | "searching" | "done" | "error";
  error: string | null;
  result_count: number;
}

export interface ProjectSummary {
  id: number;
  goal: string;
  approach: string | null;
  planner: string;
  created_at: string;
}

export interface Project extends ProjectSummary {
  approach_reason: string | null;
  needs: Need[];
}

export interface Result {
  id: number;
  need_id: number;
  source: string;
  kind: Kind;
  external_id: string;
  url: string;
  title: string;
  description: string | null;
  size_bytes: number | null;
  format: string | null;
  license: string | null;
  license_class: LicenseClass;
  relevance: number;
  quality: number;
  score: number;
  meta: { possible_mirrors?: string[]; gated?: boolean; downloads?: number; likes?: number };
  updated_at: string | null;
}

export interface Asset {
  id: number;
  result_id: number;
  title: string;
  source: string;
  storage_path: string | null;
  checksum: string | null;
  bytes_downloaded: number;
  status: "queued" | "downloading" | "done" | "error";
  error: string | null;
  created_at: string;
}

export interface Health {
  planner: "claude" | "heuristic";
  queue: string;
  kaggle_downloads: boolean;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : `${res.status} ${res.statusText}`);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  health: () => call<Health>("/health"),
  listProjects: () => call<ProjectSummary[]>("/projects"),
  getProject: (id: number) => call<Project>(`/projects/${id}`),
  createProject: (goal: string) => call<Project>("/projects", { method: "POST", body: JSON.stringify({ goal }) }),
  deleteProject: (id: number) => call<void>(`/projects/${id}`, { method: "DELETE" }),
  addNeed: (projectId: number, need: { description: string; kind: Kind; queries: string[] }) =>
    call<Need>(`/projects/${projectId}/needs`, { method: "POST", body: JSON.stringify(need) }),
  updateNeed: (id: number, patch: { description?: string; kind?: Kind; queries?: string[] }) =>
    call<Need>(`/needs/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteNeed: (id: number) => call<void>(`/needs/${id}`, { method: "DELETE" }),
  search: (projectId: number, needIds?: number[]) =>
    call<Project>(`/projects/${projectId}/search`, {
      method: "POST",
      body: JSON.stringify({ need_ids: needIds ?? null }),
    }),
  results: (projectId: number, params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
    return call<Result[]>(`/projects/${projectId}/results?${qs}`);
  },
  download: (projectId: number, resultIds: number[]) =>
    call<Asset[]>(`/projects/${projectId}/downloads`, {
      method: "POST",
      body: JSON.stringify({ result_ids: resultIds }),
    }),
  assets: (projectId: number) => call<Asset[]>(`/projects/${projectId}/assets`),
};

export function formatBytes(n: number | null | undefined): string {
  if (!n) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function sourceQueries(need: Need): string[] {
  return Object.values(need.queries)[0] ?? [];
}
