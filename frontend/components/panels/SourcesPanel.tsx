"use client";

import { useEffect, useState } from "react";
import { api, type SourceInfo } from "@/lib/api";
import { SOURCE_COLORS } from "./CrawlFeed";

export function SourcesPanel() {
  const [sources, setSources] = useState<SourceInfo[]>([]);
  useEffect(() => {
    api.sources().then(setSources).catch(() => setSources([]));
  }, []);
  const plugins = sources.filter((s) => s.origin !== "built-in");
  return (
    <div className="flex h-full flex-col gap-2 overflow-auto p-2 text-xs">
      <table className="w-full">
        <thead className="text-left muted">
          <tr><th className="py-1">source</th><th>kinds</th><th>origin</th></tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={`${s.name}-${s.origin}`} className="border-t border-[var(--border)]">
              <td className="py-1 mono" style={{ color: SOURCE_COLORS[s.name] }}>{s.name}</td>
              <td className="mono muted">{s.kinds.join(", ")}</td>
              <td className={s.error ? "text-red-400" : "muted"}>{s.error ? `error: ${s.error}` : s.origin}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        {plugins.length ? `${plugins.length} plugin connector(s) loaded. ` : "No plugins loaded. "}
        Add a Python file defining a <span className="mono">Connector</span> subclass to the <span className="mono">plugins/</span> folder (see
        <span className="mono"> plugins/zenodo.py</span>) or install a package with a <span className="mono">dataforge.connectors</span> entry point, then restart the backend.
      </p>
    </div>
  );
}
