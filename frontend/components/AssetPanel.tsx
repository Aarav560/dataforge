import { formatBytes, type Asset } from "@/lib/api";

const COLOR: Record<Asset["status"], string> = {
  queued: "text-zinc-400",
  downloading: "text-sky-400",
  done: "text-green-500",
  error: "text-red-400",
};

export function AssetPanel({ assets }: { assets: Asset[] }) {
  return (
    <div className="panel p-3">
      <div className="mb-2 text-xs uppercase tracking-wide muted">Downloads</div>
      {assets.length === 0 ? (
        <p className="text-xs muted">Select results and press Download.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {assets.map((a) => (
            <li key={a.id} className="text-xs">
              <div className="mono break-all">{a.title}</div>
              <div className="flex gap-2">
                <span className={COLOR[a.status]}>{a.status}</span>
                {(a.status === "done" || a.status === "downloading") && a.bytes_downloaded > 0 && (
                  <span className="muted">{formatBytes(a.bytes_downloaded)}</span>
                )}
              </div>
              {a.status === "done" && a.storage_path && (
                <div className="mono muted break-all text-[10px]">{a.storage_path}</div>
              )}
              {a.error && <div className="text-red-400 break-words">{a.error}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
