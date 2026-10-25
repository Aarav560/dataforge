export type Kind = "dataset" | "model" | "environment" | "code" | "video" | "paper";
export type LicenseClass = "commercial" | "non_commercial" | "research_only" | "local_only" | "unknown";

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
  meta: {
    possible_mirrors?: string[];
    gated?: boolean;
    downloads?: number;
    likes?: number;
    thumbnail?: string;
    channel?: string;
    authors?: string[];
    via_paper?: { source: string; id: string; title: string };
    links?: { source: string; kind: string; external_id: string; url: string }[];
  };
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

export interface Advice {
  recommended: boolean;
  category: string | null;
  reason: string;
  generator: string | null;
  template: string | null;
  scaffold: string | null;
  source: string;
}

export interface Template {
  id: string;
  label: string;
  description: string;
  needs_assets: boolean;
}

export interface GraphNode {
  id: string;
  type: string;
  params?: Record<string, unknown>;
}

export interface Graph {
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  layout?: Record<string, { x: number; y: number }>;
}

export interface StepParam {
  type: string;
  default: unknown;
  help: string;
  choices: string[] | null;
  required: boolean;
}

export interface StepDef {
  type: string;
  label: string;
  category: string;
  description: string;
  is_source: boolean;
  params: Record<string, StepParam>;
}

export interface Pipeline {
  id: number;
  project_id: number;
  name: string;
  template: string | null;
  graph_json: Graph;
  created_at: string;
  updated_at: string;
}

export interface ColumnStats {
  name: string;
  type: string;
  kind: "numeric" | "categorical" | "text" | "nested";
  nulls: number;
  distinct?: number;
  min?: number;
  max?: number;
  mean?: number;
  std?: number;
  avg_length?: number;
  top?: { value: string; count: number }[];
  histogram?: { from: number; to: number; count: number }[];
}

export interface Version {
  id: number;
  project_id: number;
  pipeline_id: number | null;
  parent_version_id: number | null;
  stats: { rows?: number; columns?: ColumnStats[] };
  version: number;
  status: "running" | "done" | "error";
  columns: { name: string; type: string }[];
  row_count: number;
  log: string;
  error: string | null;
  card_md: string | null;
  export_paths: Record<string, string>;
  graph_json: Graph;
  created_at: string;
  finished_at: string | null;
}

export interface Rows {
  total: number;
  offset: number;
  rows: Record<string, unknown>[];
}

export interface Stats {
  results: number;
  bytes_gathered: number;
  assets_done: number;
  workers_active: number;
  searching: number;
  downloads: { asset_id: number; bytes: number; size: number | null; status: string }[];
  running_versions: number;
  latest_version: number | null;
  rows: number;
  quota: Record<string, { remaining: number; limit: number | null; reset: string | null }>;
}

export type Preview =
  | { type: "video"; embed?: string; thumbnail?: string; mime?: string }
  | { type: "table"; columns: { name: string; type: string }[]; rows: Record<string, unknown>[]; images?: string[] }
  | { type: "markdown" | "text"; text: string }
  | { type: "paper"; abstract: string | null; authors: string[]; links: { source: string; external_id: string; url: string }[]; pdf?: string }
  | { type: "image"; mime?: string }
  | { type: "meta"; description: string | null; size_bytes: number | null; format: string | null; license: string | null }
  | { type: "binary"; size: number }
  | { type: "error"; error: string };

export interface AssetFile {
  path: string;
  size: number;
  kind: "table" | "image" | "video" | "text" | "binary";
}

export interface Rights {
  commercial: boolean | null;
  redistribution: boolean | null;
  attribution: boolean | null;
  share_alike: boolean | null;
}

export interface MatrixRow {
  result_id: number;
  title: string;
  source: string;
  license: string | null;
  license_class: LicenseClass;
  rights: Rights;
}

export interface LiveEvent {
  type: string;
  ts: number;
  replay?: boolean;
  [key: string]: unknown;
}

export interface Diff {
  base: { id: number; version: number };
  target: { id: number; version: number };
  rows: { before: number; after: number; delta: number; added?: number; removed?: number; unchanged?: number };
  columns: {
    added: { name: string; type: string }[];
    removed: { name: string; type: string }[];
    type_changed: { name: string; before: string; after: string }[];
  };
  compared_columns: string[];
  sample_added?: Record<string, unknown>[];
  sample_removed?: Record<string, unknown>[];
}

export interface ExportCheck {
  allowed: boolean;
  blockers: string[];
  warnings: string[];
  needs_acknowledgement: boolean;
}

export interface ExportJob {
  id: number;
  version_id: number;
  format: string;
  status: "queued" | "running" | "done" | "error";
  push_repo: string | null;
  private: boolean;
  files: number;
  hub_url: string | null;
  warnings: string[];
  error: string | null;
  created_at: string;
}

export interface ReviewItem {
  id: number;
  version_id: number;
  row_index: number;
  payload: { text?: string; asset_id?: number; path?: string };
  label_column: string;
  proposed_label: string | null;
  confidence: number | null;
  status: "pending" | "accepted" | "corrected" | "rejected";
  final_label: string | null;
}

export interface ReviewQueue {
  counts: Record<string, number>;
  labels: string[];
  items: ReviewItem[];
}

export interface IndexStatus {
  entries: { source: string; kind: string; count: number; synced_at: string | null }[];
  cached_queries: number;
  embedding_model: string;
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
  advice: (projectId: number, refresh = false) =>
    call<Advice>(`/projects/${projectId}/synthetic${refresh ? "?refresh=true" : ""}`),
  templates: () => call<Template[]>("/pipeline-templates"),
  steps: () => call<StepDef[]>("/pipeline-steps"),
  pipelines: (projectId: number) => call<Pipeline[]>(`/projects/${projectId}/pipelines`),
  createPipeline: (projectId: number, body: { template?: string; asset_ids?: number[]; graph?: Graph; name?: string }) =>
    call<Pipeline>(`/projects/${projectId}/pipelines`, { method: "POST", body: JSON.stringify(body) }),
  updatePipeline: (id: number, body: { name?: string; graph?: Graph }) =>
    call<Pipeline>(`/pipelines/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deletePipeline: (id: number) => call<void>(`/pipelines/${id}`, { method: "DELETE" }),
  runPipeline: (id: number) => call<Version>(`/pipelines/${id}/run`, { method: "POST" }),
  versions: (projectId: number) => call<Version[]>(`/projects/${projectId}/versions`),
  stats: (projectId: number) => call<Stats>(`/projects/${projectId}/stats`),
  preview: (resultId: number) => call<Preview>(`/results/${resultId}/preview`),
  licenseMatrix: (resultIds: number[]) =>
    call<MatrixRow[]>("/license-matrix", { method: "POST", body: JSON.stringify({ result_ids: resultIds }) }),
  assetFiles: (assetId: number) => call<AssetFile[]>(`/assets/${assetId}/files`),
  assetPreview: (assetId: number, path: string) =>
    call<Preview>(`/assets/${assetId}/preview?path=${encodeURIComponent(path)}`),
  diff: (versionId: number, against?: number) =>
    call<Diff>(`/versions/${versionId}/diff${against ? `?against=${against}` : ""}`),
  regenerateCard: (versionId: number) => call<Version>(`/versions/${versionId}/card`, { method: "POST" }),
  exportFormats: () => call<Record<string, string>>("/export-formats"),
  exportCheck: (versionId: number, format: string, push: boolean, acknowledge: boolean) =>
    call<ExportCheck>(`/versions/${versionId}/export-check?format=${format}&push=${push}&acknowledge=${acknowledge}`),
  startExport: (versionId: number, body: { format: string; push_to_hub?: boolean; repo_id?: string; private?: boolean; acknowledge_unknown_licenses?: boolean }) =>
    call<ExportJob>(`/versions/${versionId}/exports`, { method: "POST", body: JSON.stringify(body) }),
  exports: (versionId: number) => call<ExportJob[]>(`/versions/${versionId}/exports`),
  reviews: (versionId: number, status = "pending", offset = 0, limit = 50) =>
    call<ReviewQueue>(`/versions/${versionId}/reviews?status=${status}&offset=${offset}&limit=${limit}`),
  decide: (reviewId: number, action: "accept" | "correct" | "reject" | "reset", label?: string) =>
    call<ReviewItem>(`/reviews/${reviewId}`, { method: "POST", body: JSON.stringify({ action, label }) }),
  applyReviews: (versionId: number) =>
    call<{ queued: boolean; reviews: number }>(`/versions/${versionId}/apply-reviews`, { method: "POST" }),
  indexStatus: () => call<IndexStatus>("/index"),
  syncIndex: () => call<{ queued: boolean }>("/index/sync", { method: "POST" }),
  rows: (versionId: number, offset = 0, limit = 50) =>
    call<Rows>(`/versions/${versionId}/rows?offset=${offset}&limit=${limit}`),
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

export function assetFileUrl(assetId: number, path: string): string {
  return `/api/assets/${assetId}/file?path=${encodeURIComponent(path)}`;
}

export function wsUrl(projectId: number): string {
  const base = process.env.NEXT_PUBLIC_WS_URL;
  if (base) return `${base.replace(/\/$/, "")}/api/projects/${projectId}/ws`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.hostname}:8000/api/projects/${projectId}/ws`;
}

export function exportDownloadUrl(exportId: number): string {
  return `/api/exports/${exportId}/download`;
}
