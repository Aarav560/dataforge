"use client";

import { useEffect, useState } from "react";
import { api, assetFileUrl, formatBytes, type AssetFile, type Preview } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { DataTable } from "../DataTable";
import { LicenseBadge } from "../LicenseBadge";

export function PreviewPane() {
  const { focused, results, assets } = useWorkspace();
  const result = results.find((r) => r.id === focused);
  const asset = [...assets].find((a) => a.result_id === focused && a.status === "done");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"remote" | "files">("remote");

  useEffect(() => {
    setPreview(null);
    setTab("remote");
    if (focused === null) return;
    let cancelled = false;
    setLoading(true);
    api
      .preview(focused)
      .then((p) => !cancelled && setPreview(p))
      .catch((e) => !cancelled && setPreview({ type: "error", error: (e as Error).message }))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [focused]);

  if (!result) return <div className="p-4 text-sm muted">Click a result or a graph node to preview it.</div>;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border)] p-2">
        <a href={result.url} target="_blank" rel="noreferrer" className="mono text-sm font-medium hover:underline break-all">
          {result.title}
        </a>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="mono">{result.source}</span>
          <LicenseBadge cls={result.license_class} license={result.license} />
          <span className="muted">{formatBytes(result.size_bytes)}</span>
        </div>
        {asset && (
          <div className="mt-2 flex gap-1">
            <button className={`btn ${tab === "remote" ? "border-[var(--accent)]" : ""}`} onClick={() => setTab("remote")}>
              Preview
            </button>
            <button className={`btn ${tab === "files" ? "border-[var(--accent)]" : ""}`} onClick={() => setTab("files")}>
              Downloaded files
            </button>
          </div>
        )}
      </div>
      <div className="flex-1 overflow-auto p-2">
        {tab === "files" && asset ? (
          <AssetBrowser assetId={asset.id} />
        ) : loading ? (
          <div className="text-xs muted">Loading preview…</div>
        ) : preview ? (
          <PreviewView preview={preview} />
        ) : null}
      </div>
    </div>
  );
}

export function PreviewView({ preview, assetId, path }: { preview: Preview; assetId?: number; path?: string }) {
  switch (preview.type) {
    case "video":
      if (preview.embed)
        return (
          <iframe
            src={preview.embed}
            className="aspect-video w-full rounded"
            allow="encrypted-media; picture-in-picture"
            allowFullScreen
            title="video preview"
          />
        );
      return assetId !== undefined && path ? <video src={assetFileUrl(assetId, path)} controls className="w-full rounded" /> : null;
    case "image":
      // eslint-disable-next-line @next/next/no-img-element
      return assetId !== undefined && path ? <img src={assetFileUrl(assetId, path)} alt={path} className="max-w-full rounded" /> : null;
    case "table":
      return (
        <div className="flex flex-col gap-2">
          {preview.images && preview.images.length > 0 && (
            <div className="grid grid-cols-4 gap-1">
              {preview.images.map((src) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={src} src={src} alt="" className="aspect-square w-full rounded object-cover" loading="lazy" />
              ))}
            </div>
          )}
          <div className="text-[11px] muted mono">
            schema: {preview.columns.map((c) => `${c.name}:${c.type}`).join(", ")}
          </div>
          <DataTable rows={preview.rows} />
        </div>
      );
    case "markdown":
    case "text":
      return <pre className="whitespace-pre-wrap break-words text-[11px] mono">{preview.text}</pre>;
    case "paper":
      return (
        <div className="flex flex-col gap-2 text-xs">
          {preview.authors.length > 0 && <div className="muted">{preview.authors.join(", ")}</div>}
          <p>{preview.abstract}</p>
          {preview.links.length > 0 && (
            <div>
              <div className="muted">Linked resources</div>
              {preview.links.map((l) => (
                <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="block mono text-sky-400 hover:underline">
                  {l.source}: {l.external_id}
                </a>
              ))}
            </div>
          )}
          {preview.pdf && (
            <a href={preview.pdf} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
              Open PDF
            </a>
          )}
        </div>
      );
    case "meta":
      return <p className="text-xs muted">{preview.description || "No preview available for this source."}</p>;
    case "binary":
      return <p className="text-xs muted">Binary file ({formatBytes(preview.size)}).</p>;
    case "error":
      return <p className="text-xs text-amber-400">{preview.error}</p>;
  }
}

function AssetBrowser({ assetId }: { assetId: number }) {
  const [files, setFiles] = useState<AssetFile[]>([]);
  const [open, setOpen] = useState<{ path: string; preview: Preview } | null>(null);

  useEffect(() => {
    api.assetFiles(assetId).then(setFiles).catch(() => setFiles([]));
  }, [assetId]);

  const show = async (f: AssetFile) => {
    const preview = await api.assetPreview(assetId, f.path).catch((e) => ({ type: "error", error: String(e) }) as Preview);
    setOpen({ path: f.path, preview });
  };

  return (
    <div className="flex flex-col gap-2">
      <ul className="max-h-40 overflow-auto text-[11px] mono">
        {files.map((f) => (
          <li key={f.path}>
            <button className={`w-full text-left hover:text-[var(--accent)] ${open?.path === f.path ? "text-[var(--accent)]" : ""}`} onClick={() => show(f)}>
              {f.path} <span className="muted">· {f.kind} · {formatBytes(f.size)}</span>
            </button>
          </li>
        ))}
      </ul>
      {open && <PreviewView preview={open.preview} assetId={assetId} path={open.path} />}
    </div>
  );
}
