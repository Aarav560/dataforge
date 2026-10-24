// Thin client for the DataForge FastAPI backend.
export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";
export const WS = API.replace(/^http/, "ws");

export type Kind = "dataset" | "model" | "environment" | "paper" | "video" | "image" | "web" | "code";

export interface Need {
  id: string;
  kind: Kind;
  description: string;
  queries: string[];
  sources: string[];
  status: string;
  priority: number;
  enabled?: boolean;
  count?: number;
}

export interface Plan {
  goal: string;
  subject: string;
  domain: string;
  approach: { primary: string; alternatives: string[]; rationale: string };
  needs: Need[];
  synthetic: {
    recommended: boolean;
    score: number;
    generator: string | null;
    generator_info: { label: string; schema: string; params: Record<string, unknown> } | null;
    rationale: string;
    scaffold: string | null;
  };
  planner: string;
}

export interface Project {
  id: string;
  goal: string;
  plan: Plan;
  created_at: number;
  results?: number;
  versions?: number;
}

export interface Result {
  id: string;
  need_id: string | null;
  source: string;
  kind: Kind;
  url: string;
  title: string;
  description: string;
  size_bytes: number | null;
  format: string | null;
  license: string | null;
  license_class: string;
  relevance: number;
  quality: number;
  score: number;
  meta: Record<string, any>;
  dup_of: string | null;
  selected: number | boolean;
}

export interface Asset {
  id: string;
  result_id: string;
  storage_path: string;
  size_bytes: number;
  status: string;
  error: string | null;
  title: string;
  source: string;
  kind: string;
  license_class: string;
}

export interface Version {
  id: string;
  version: number;
  row_count: number;
  schema: { name: string; dtype: string }[];
  export_paths: Record<string, string>;
  card_md?: string;
  stats?: any;
  sources?: any[];
  created_at: number;
}

export interface Connector { name: string; label: string; kinds: string[]; requires: string | null; available: boolean }

export interface FeedEvent {
  type: string;
  ts: number;
  [k: string]: any;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {}
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}) => req<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => req<{ llm: { model: string | null; available: boolean }; connectors: Connector[]; workers_active: number }>("/api/health"),
  stats: (pid?: string) => req<any>(`/api/stats${pid ? `?project_id=${pid}` : ""}`),
  projects: () => req<Project[]>("/api/projects"),
  project: (id: string) => req<Project & { jobs: any[] }>(`/api/projects/${id}`),
  createProject: (goal: string, use_llm: boolean) => post<Project>("/api/projects", { goal, use_llm }),
  savePlan: (id: string, plan: Plan) => req<Plan>(`/api/projects/${id}/plan`, { method: "PUT", body: JSON.stringify({ plan }) }),
  replan: (id: string, use_llm = true) => post<Plan>(`/api/projects/${id}/replan?use_llm=${use_llm}`),
  similar: (id: string) => req<{ id: string; goal: string; similarity: number }[]>(`/api/projects/${id}/similar`),
  search: (id: string, need_ids?: string[], llm_judge = false) => post<{ job_id: string }>(`/api/projects/${id}/search`, { need_ids, llm_judge }),
  results: (id: string) => req<Result[]>(`/api/projects/${id}/results`),
  select: (rid: string, selected: boolean) => post(`/api/results/${rid}/select`, { selected }),
  preview: (rid: string) => req<any>(`/api/results/${rid}/preview`),
  gather: (id: string, result_ids?: string[]) => post<{ job_id: string }>(`/api/projects/${id}/gather`, { result_ids }),
  assets: (id: string) => req<Asset[]>(`/api/projects/${id}/assets`),
  licenseMatrix: (id: string, use = "research") => req<any>(`/api/projects/${id}/license-matrix?intended_use=${use}`),
  generators: () => req<Record<string, { label: string; schema: string; params: Record<string, unknown> }>>("/api/synthetic/generators"),
  synthesize: (id: string, generator: string, params: Record<string, unknown>) => post<{ job_id: string }>(`/api/projects/${id}/synthetic`, { generator, params }),
  templates: () => req<{ templates: Record<string, { label: string; graph: any }>; node_types: any[] }>("/api/pipelines/templates"),
  savePipeline: (id: string, body: { template?: string; graph?: any; id?: string }) => post<any>(`/api/projects/${id}/pipelines`, body),
  pipelines: (id: string) => req<any[]>(`/api/projects/${id}/pipelines`),
  runPipeline: (plid: string) => post<{ job_id: string }>(`/api/pipelines/${plid}/run`),
  versions: (id: string) => req<Version[]>(`/api/projects/${id}/versions`),
  version: (vid: string) => req<Version>(`/api/versions/${vid}`),
  rows: (vid: string, offset = 0, limit = 100) => req<{ columns: string[]; rows: any[] }>(`/api/versions/${vid}/rows?offset=${offset}&limit=${limit}`),
  diff: (a: string, b: string) => req<any>(`/api/versions/${a}/diff/${b}`),
  exportVersion: (vid: string, format: string, intended_use = "research", push_repo?: string) =>
    post<{ format: string; path: string; warnings: string[] }>(`/api/versions/${vid}/export`, { format, intended_use, push_repo }),
  downloadUrl: (vid: string, fmt: string) => `${API}/api/versions/${vid}/download/${fmt}`,
  review: (vid: string, decisions: { row: number; label: string }[]) => post<any>(`/api/versions/${vid}/review`, { decisions }),
  baseline: (vid: string, target?: string) => post<any>(`/api/versions/${vid}/baseline`, { target }),
  fileUrl: (path: string) => `${API}/api/files?path=${encodeURIComponent(path)}`,
};

export function fmtBytes(n?: number | null): string {
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

export function fmtNum(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
}

export const LICENSE_CHIP: Record<string, string> = {
  commercial_ok: "pill-ok", copyleft: "pill-ok", non_commercial: "pill-warn", research_only: "pill-warn",
  no_derivatives: "pill-warn", unknown: "pill-warn", local_only: "pill-bad",
};
export const LICENSE_LABEL: Record<string, string> = {
  commercial_ok: "Commercial OK", copyleft: "Share-alike", non_commercial: "Non-commercial", research_only: "Research only",
  no_derivatives: "No derivatives", unknown: "Check licence", local_only: "Local use only",
};

// Kind colours: distinguishable on porcelain and on dark, calm enough to sit beside the ember/cobalt accents.
export const KIND_COLOR: Record<string, string> = {
  dataset: "#2f48e0", model: "#7a4bd6", environment: "#1d8556", paper: "#a8700f", video: "#c0392b",
  image: "#c2418f", web: "#5b6576", code: "#1f7fa8", goal: "#101826", need: "#5b6576",
};

export const KIND_LABEL: Record<string, string> = {
  dataset: "Datasets", model: "Models", environment: "Environments", paper: "Papers", video: "Video", image: "Images", web: "Web", code: "Code",
};

export const SOURCE_LABEL: Record<string, string> = {
  hf_datasets: "Hugging Face", hf_models: "Hugging Face", kaggle: "Kaggle", openml: "OpenML", uci: "UCI", zenodo: "Zenodo",
  github: "GitHub", github_code: "GitHub", environments: "Env catalog", arxiv: "arXiv", semantic_scholar: "Semantic Scholar",
  openalex: "OpenAlex", youtube: "YouTube", internet_archive: "Internet Archive", openverse: "Openverse", wikimedia: "Wikimedia",
  web: "Web", synthetic: "Generated",
};
