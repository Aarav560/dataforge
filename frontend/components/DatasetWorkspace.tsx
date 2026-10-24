"use client";
import { useEffect, useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Asset, LICENSE_CHIP, LICENSE_LABEL, SOURCE_LABEL, Version } from "@/lib/api";

const FORMATS: [string, string][] = [
  ["parquet", "Parquet"], ["csv", "CSV"], ["jsonl", "JSONL"], ["hf", "Hugging Face"], ["coco", "COCO"], ["yolo", "YOLO"], ["tfrecord", "TFRecord"],
];
const TIP = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12, color: "var(--ink)" };

/** Stage 06 — versions, stats, dataset card, exports, diffs, review queue, baseline (plan §3.6). */
export function DatasetWorkspace({ versions, onChange }: { versions: Version[]; assets: Asset[]; onChange: () => void }) {
  const [vid, setVid] = useState<string | null>(null);
  const [v, setV] = useState<Version | null>(null);
  const [tab, setTab] = useState<"overview" | "rows" | "card" | "review">("overview");
  const [cmp, setCmp] = useState("");
  const [diff, setDiff] = useState<any>(null);

  useEffect(() => { if ((!vid || !versions.some((x) => x.id === vid)) && versions[0]) setVid(versions[0].id); }, [versions, vid]);
  useEffect(() => { if (vid) { setDiff(null); api.version(vid).then(setV); } }, [vid, versions]);
  useEffect(() => { if (vid && cmp) api.diff(cmp, vid).then(setDiff).catch(() => setDiff(null)); else setDiff(null); }, [cmp, vid]);

  if (!versions.length) {
    return (
      <div className="h-full grid place-items-center px-6">
        <div className="text-center max-w-md">
          <h2 className="display text-[24px] font-semibold">No dataset yet</h2>
          <p className="text-ink-2 mt-2">Run a pipeline in Transform or generate data in Synthesize. Every run is saved here as a new version.</p>
        </div>
      </div>
    );
  }
  const s = v?.stats;
  const reviewN = s?.review?.length ?? 0;

  return (
    <div className="h-full flex">
      <aside className="w-56 shrink-0 border-r border-line bg-surface overflow-auto py-3 hidden md:block">
        <p className="eyebrow px-4 mb-2">Versions</p>
        <ol>
          {versions.map((x) => (
            <li key={x.id}>
              <button onClick={() => setVid(x.id)} aria-current={vid === x.id}
                className={`w-full text-left px-4 py-2.5 border-l-2 transition-colors ${vid === x.id ? "border-accent bg-accent-soft/50" : "border-transparent hover:bg-sunken"}`}>
                <div className="flex items-baseline gap-2"><span className="display font-semibold text-[15px]">v{x.version}</span>
                  <span className="text-[12px] text-ink-3 ml-auto">{new Date(x.created_at * 1000).toLocaleDateString()}</span></div>
                <div className="text-[12px] text-ink-2 tabular-nums">{x.row_count.toLocaleString()} rows · {x.schema.length} columns</div>
              </button>
            </li>
          ))}
        </ol>
      </aside>

      <div className="flex-1 min-w-0 overflow-auto">
        {!v ? <p className="p-8 text-ink-3">Loading…</p> : (
          <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
            <header className="flex flex-wrap items-end gap-6">
              <div>
                <p className="eyebrow">Version {v.version}</p>
                <div className="flex items-baseline gap-6 mt-1">
                  <Figure n={v.row_count.toLocaleString()} label="rows" />
                  <Figure n={String(v.schema.length)} label="columns" />
                  <Figure n={String(v.sources?.length ?? 0)} label="sources" />
                  {s?.pii && <Figure n={String(Object.values(s.pii as Record<string, number>).reduce((a, b) => a + b, 0))} label="PII redactions" />}
                </div>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[12.5px] text-ink-3">Compare with</span>
                <select className="input" value={cmp} onChange={(e) => setCmp(e.target.value)} aria-label="Compare with version">
                  <option value="">—</option>
                  {versions.filter((x) => x.id !== vid).map((x) => <option key={x.id} value={x.id}>v{x.version}</option>)}
                </select>
              </div>
            </header>

            {diff && <DiffView d={diff} />}

            <div className="grid xl:grid-cols-[1fr_340px] gap-6 items-start">
              <div className="space-y-4 min-w-0">
                <div className="seg">
                  <button data-active={tab === "overview"} onClick={() => setTab("overview")}>Overview</button>
                  <button data-active={tab === "rows"} onClick={() => setTab("rows")}>Rows</button>
                  <button data-active={tab === "card"} onClick={() => setTab("card")}>Dataset card</button>
                  <button data-active={tab === "review"} onClick={() => setTab("review")}>Review{reviewN ? ` (${reviewN})` : ""}</button>
                </div>
                {tab === "overview" && <Overview v={v} />}
                {tab === "rows" && <RowsView vid={v.id} />}
                {tab === "card" && <pre className="card p-5 text-[12.5px] whitespace-pre-wrap leading-relaxed mono overflow-auto">{v.card_md}</pre>}
                {tab === "review" && <ReviewQueue v={v} onDone={onChange} />}
              </div>
              <div className="space-y-4">
                <ExportCard v={v} onExported={() => api.version(v.id).then(setV)} />
                <BaselineCard v={v} />
                <SourcesCard v={v} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Figure({ n, label }: { n: string; label: string }) {
  return <div><div className="display text-[28px] font-semibold tabular-nums leading-none">{n}</div><div className="text-[12px] text-ink-3 mt-1">{label}</div></div>;
}

function Overview({ v }: { v: Version }) {
  const s = v.stats;
  return (
    <div className="space-y-4">
      <SampleGrid sample={s?.sample ?? []} />
      {s?.class_balance && (
        <section className="card p-4">
          <h3 className="font-semibold text-[14px]">Class balance <span className="text-ink-3 font-normal mono text-[12px]">{s.class_balance.column}</span></h3>
          <div className="h-44 mt-2">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={s.class_balance.counts} margin={{ left: -12, right: 4, top: 4 }}>
                <XAxis dataKey="value" tick={{ fontSize: 11, fill: "var(--ink-3)" }} interval={0} angle={-25} textAnchor="end" height={44} tickLine={false} axisLine={{ stroke: "var(--line)" }} />
                <YAxis tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={TIP} cursor={{ fill: "var(--sunken)" }} />
                <Bar dataKey="n" name="rows" fill="var(--accent)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}
      <section>
        <h3 className="font-semibold text-[14px] mb-2">Columns</h3>
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {(s?.columns ?? []).map((c: any) => <ColumnCard key={c.name} c={c} rows={v.row_count} />)}
        </div>
      </section>
      {s?.notes?.length > 0 && (
        <section className="card p-4">
          <h3 className="font-semibold text-[14px] mb-2">How this version was built</h3>
          <ol className="list-decimal pl-5 space-y-1 text-[13px] text-ink-2">{s.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ol>
        </section>
      )}
    </div>
  );
}

function ColumnCard({ c, rows }: { c: any; rows: number }) {
  const nullPct = rows ? (c.nulls / rows) * 100 : 0;
  return (
    <div className="card p-3.5">
      <div className="flex items-center gap-2">
        <span className="mono text-[13px] font-medium truncate" title={c.name}>{c.name}</span>
        <span className="ml-auto pill mono">{c.dtype.toLowerCase()}</span>
      </div>
      <div className="text-[12px] text-ink-3 mt-1 tabular-nums">
        {c.unique != null ? `${c.unique.toLocaleString()} unique` : ""}{nullPct > 0 ? <span className={nullPct > 20 ? "text-warn" : ""}> · {nullPct.toFixed(1)}% empty</span> : ""}
      </div>
      {c.hist && (
        <div className="h-12 mt-2">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={c.hist} margin={{ top: 0, left: 0, right: 0, bottom: 0 }}>
              <Tooltip contentStyle={TIP} cursor={{ fill: "var(--sunken)" }} labelFormatter={(i) => `≤ ${c.hist[i as number]?.x}`} />
              <Bar dataKey="n" name="rows" fill="var(--ink-3)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      {c.min != null && <div className="mono text-[11.5px] text-ink-3 mt-1">{fmtN(c.min)} – {fmtN(c.max)} · mean {fmtN(c.mean)}</div>}
      {c.top && (
        <ul className="mt-2 space-y-1">
          {c.top.slice(0, 4).map((t: any, i: number) => {
            const pct = rows ? (t.n / rows) * 100 : 0;
            return (
              <li key={i} className="relative text-[12px] rounded overflow-hidden">
                <span className="absolute inset-y-0 left-0 bg-accent-soft" style={{ width: `${Math.max(2, pct)}%` }} aria-hidden />
                <span className="relative flex gap-2 px-1.5 py-0.5"><span className="truncate flex-1 mono" title={t.value}>{t.value}</span><span className="text-ink-3 tabular-nums">{t.n.toLocaleString()}</span></span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
const fmtN = (x: number) => (Math.abs(x) >= 1e5 ? x.toExponential(2) : Number(x.toFixed(3)).toLocaleString());

function SampleGrid({ sample }: { sample: any[] }) {
  const key = sample[0] && ["image_path", "frame_path"].find((k) => k in sample[0]);
  if (key) {
    return (
      <section className="card p-4">
        <h3 className="font-semibold text-[14px] mb-2">Sample</h3>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))" }}>
          {sample.slice(0, 18).map((r, i) => (
            <figure key={i}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={api.fileUrl(r[key])} alt={String(r.label ?? `sample ${i + 1}`)} className="w-full aspect-square object-cover rounded-md border border-line bg-sunken" loading="lazy" />
              <figcaption className="text-[11px] text-ink-2 truncate mt-0.5">{r.label ?? r.action ?? ""}</figcaption>
            </figure>
          ))}
        </div>
      </section>
    );
  }
  const boardKey = sample[0] && ["board", "state"].find((k) => typeof sample[0][k] === "string" && /^[01]{200}$/.test(sample[0][k]));
  if (!boardKey) return null;
  return (
    <section className="card p-4">
      <h3 className="font-semibold text-[14px] mb-3">Sample boards</h3>
      <div className="flex gap-4 overflow-auto pb-1">
        {sample.slice(0, 10).map((r, i) => (
          <figure key={i} className="shrink-0">
            <div className="grid gap-px p-1 rounded-md bg-sunken border border-line" style={{ gridTemplateColumns: "repeat(10, 7px)" }} aria-label={`Board ${i + 1}`}>
              {(r[boardKey] as string).split("").map((b, j) => <div key={j} className="w-[7px] h-[7px] rounded-[1.5px]" style={{ background: b === "1" ? "var(--ink)" : "var(--line)" }} />)}
            </div>
            <figcaption className="text-[11px] text-ink-2 text-center mt-1 mono">
              {r.piece ?? ""}{r.rotation != null ? ` · r${r.rotation}` : ""}{r.column != null ? ` · col ${r.column}` : ""}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

function ExportCard({ v, onExported }: { v: Version; onExported: () => void }) {
  const [fmt, setFmt] = useState("parquet");
  const [use, setUse] = useState("research");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // A format is downloadable once it has been exported (exports are zipped; the raw parquet path is not).
  const ready = !!v.export_paths?.[fmt]?.endsWith(".zip");

  const go = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.exportVersion(v.id, fmt, use, fmt === "hf" && repo ? repo : undefined);
      setMsg({ ok: true, text: `Exported as ${FORMATS.find((f) => f[0] === fmt)?.[1]}.${r.warnings.length ? " " + r.warnings.join(" · ") : ""}` });
      onExported();
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    setBusy(false);
  };

  return (
    <section className="card p-4 space-y-3">
      <h3 className="font-semibold">Export</h3>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Format">
        {FORMATS.map(([k, l]) => (
          <button key={k} role="radio" aria-checked={fmt === k} onClick={() => { setFmt(k); setMsg(null); }}
            className={`pill !h-7 !px-3 !text-[12.5px] transition-colors ${fmt === k ? "!bg-ink !text-surface !border-ink" : "hover:border-ink-3"}`}>{l}</button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[12.5px] text-ink-3">For</span>
        <div className="seg"><button data-active={use === "research"} onClick={() => setUse("research")}>Research</button><button data-active={use === "commercial"} onClick={() => setUse("commercial")}>Commercial use</button></div>
      </div>
      {fmt === "hf" && <input className="input w-full" placeholder="Push to Hub as user/name (optional)" value={repo} onChange={(e) => setRepo(e.target.value)} />}
      <div className="flex items-center gap-2">
        <button className="btn btn-primary flex-1 justify-center" onClick={go} disabled={busy}>{busy ? "Exporting…" : "Export"}</button>
        {ready ? (
          <a className="btn" href={api.downloadUrl(v.id, fmt)}>Download .zip</a>
        ) : null}
      </div>
      {msg && <p className={`text-[12.5px] ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.text}</p>}
    </section>
  );
}

function BaselineCard({ v }: { v: Version }) {
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [b, setB] = useState<any>(null);
  useEffect(() => { setB(null); }, [v.id]);
  const run = async () => {
    setBusy(true); setB(null);
    try { setB(await api.baseline(v.id, target || undefined)); } catch (e) { setB({ error: e instanceof Error ? e.message : String(e) }); }
    setBusy(false);
  };
  return (
    <section className="card p-4 space-y-3">
      <div>
        <h3 className="font-semibold">Quick baseline</h3>
        <p className="text-[12.5px] text-ink-2 mt-0.5">Trains a small linear model to check the data carries signal.</p>
      </div>
      <div className="flex gap-2">
        <select className="input flex-1 min-w-0" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Column to predict">
          <option value="">Predict: auto-detect</option>
          {v.schema.filter((c) => !c.name.startsWith("_") && c.name !== "split").map((c) => <option key={c.name} value={c.name}>Predict: {c.name}</option>)}
        </select>
        <button className="btn" onClick={run} disabled={busy}>{busy ? "Training…" : "Train"}</button>
      </div>
      {b?.error && <p className="text-[12.5px] text-bad">{b.error}</p>}
      {b && !b.error && (
        <div className="rounded-lg bg-sunken p-3 text-[12.5px] space-y-1">
          {b.accuracy != null ? (
            <div className="flex items-baseline gap-2">
              <span className="display text-[24px] font-semibold tabular-nums">{(b.accuracy * 100).toFixed(1)}%</span>
              <span className="text-ink-2">accuracy vs {(b.majority_baseline * 100).toFixed(1)}% always guessing the most common class</span>
            </div>
          ) : b.r2 != null && (
            <div className="flex items-baseline gap-2">
              <span className="display text-[24px] font-semibold tabular-nums">R² {b.r2.toFixed(2)}</span>
              <span className="text-ink-2">mean abs. error {b.mae.toFixed(3)}</span>
            </div>
          )}
          <p className="text-ink-3">{b.model} · predicting <span className="mono">{b.target}</span> · {b.train_rows.toLocaleString()} train / {b.test_rows.toLocaleString()} test rows</p>
          <p className="text-ink-3 truncate" title={b.top_features.join(", ")}>Strongest features: {b.top_features.slice(0, 5).join(", ")}</p>
        </div>
      )}
    </section>
  );
}

function SourcesCard({ v }: { v: Version }) {
  const src = v.sources ?? [];
  return (
    <section className="card p-4">
      <h3 className="font-semibold mb-2">Sources</h3>
      {src.length === 0 ? <p className="text-[12.5px] text-ink-2">Fully generated — no external sources.</p> : (
        <ul className="space-y-2">
          {src.map((s: any) => (
            <li key={s.id} className="text-[12.5px]">
              <a href={s.url} target="_blank" rel="noreferrer noopener" className="font-medium hover:text-accent truncate block" title={s.title}>{s.title}</a>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-ink-3">{SOURCE_LABEL[s.source] ?? s.source}</span>
                <span className={`pill ${LICENSE_CHIP[s.license_class] ?? "pill-warn"}`}>{LICENSE_LABEL[s.license_class] ?? "Check licence"}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RowsView({ vid }: { vid: string }) {
  const [off, setOff] = useState(0);
  const [d, setD] = useState<{ columns: string[]; rows: any[] } | null>(null);
  useEffect(() => { api.rows(vid, off, 100).then(setD); }, [vid, off]);
  if (!d) return <p className="text-ink-3">Loading…</p>;
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
        <span className="text-[12.5px] text-ink-2 tabular-nums">Rows {off + 1}–{off + d.rows.length}</span>
        <button className="btn btn-sm ml-auto" disabled={!off} onClick={() => setOff(Math.max(0, off - 100))}>Previous</button>
        <button className="btn btn-sm" disabled={d.rows.length < 100} onClick={() => setOff(off + 100)}>Next</button>
      </div>
      <div className="overflow-auto max-h-[60vh]">
        <table className="mono text-[11.5px] w-full">
          <thead className="sticky top-0 bg-sunken"><tr>{d.columns.map((c) => <th key={c} className="px-2.5 py-2 text-left font-medium text-ink-2 whitespace-nowrap">{c}</th>)}</tr></thead>
          <tbody>{d.rows.map((r, i) => <tr key={i} className="border-t border-line hover:bg-sunken">{d.columns.map((c) => <td key={c} className="px-2.5 py-1.5 max-w-56 truncate" title={String(r[c])}>{r[c] == null ? <span className="text-ink-3">∅</span> : typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c])}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

function ReviewQueue({ v, onDone }: { v: Version; onDone: () => void }) {
  const items: any[] = v.stats?.review ?? [];
  const [labels, setLabels] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  if (!items.length) return <p className="card p-5 text-[13px] text-ink-2">Nothing to review. Rows the local LLM labels with low confidence wait here for you to confirm or correct.</p>;
  const submit = async () => {
    setSaving(true);
    await api.review(v.id, items.map((it) => ({ row: it.row, label: labels[it.row] ?? it.suggested })));
    setSaving(false);
    onDone();
  };
  return (
    <div className="space-y-2">
      {items.map((it) => (
        <div key={it.row} className="card p-4">
          <p className="text-[12px] text-ink-3">Row {it.row} · model is {(it.confidence * 100).toFixed(0)}% sure</p>
          <p className="my-2 text-[13.5px]">{it.text}</p>
          <div className="flex flex-wrap gap-1.5">
            {it.labels.map((l: string) => {
              const on = (labels[it.row] ?? it.suggested) === l;
              return <button key={l} onClick={() => setLabels({ ...labels, [it.row]: l })} aria-pressed={on}
                className={`pill !h-7 !px-3 !text-[12.5px] ${on ? "pill-accent" : "hover:border-ink-3"}`}>{l}</button>;
            })}
          </div>
        </div>
      ))}
      <button className="btn btn-primary" onClick={submit} disabled={saving}>{saving ? "Saving…" : `Confirm ${items.length} labels as a new version`}</button>
    </div>
  );
}

function DiffView({ d }: { d: any }) {
  return (
    <div className="card p-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]">
      <span className="font-semibold">v{d.from} → v{d.to}</span>
      <span className="tabular-nums">{d.rows.from.toLocaleString()} → {d.rows.to.toLocaleString()} rows</span>
      {d.rows_added != null && <span className="text-ok tabular-nums">+{d.rows_added.toLocaleString()} added</span>}
      {d.rows_removed != null && <span className="text-bad tabular-nums">−{d.rows_removed.toLocaleString()} removed</span>}
      {d.rows_changed != null && <span className="text-warn tabular-nums">{d.rows_changed.toLocaleString()} changed</span>}
      {d.rows_unchanged != null && <span className="text-ink-3 tabular-nums">{d.rows_unchanged.toLocaleString()} unchanged</span>}
      {d.key && <span className="text-ink-3">matched by <span className="mono">{d.key}</span></span>}
      {d.columns_added.length > 0 && <span className="text-ok">+ {d.columns_added.join(", ")}</span>}
      {d.columns_removed.length > 0 && <span className="text-bad">− {d.columns_removed.join(", ")}</span>}
      {d.type_changes.map((t: any) => <span key={t.column} className="text-warn">{t.column}: {t.from} → {t.to}</span>)}
      {d.values_changed && Object.keys(d.values_changed).length > 0 && (
        <div className="w-full text-[12.5px] text-ink-2">
          Changed values: {Object.entries(d.values_changed as Record<string, number>).map(([c, n]) => `${c} (${n.toLocaleString()})`).join(" · ")}
        </div>
      )}
      {d.label_changes?.length > 0 && (
        <ul className="w-full text-[12.5px] space-y-0.5">
          {d.label_changes.map((l: any, i: number) => (
            <li key={i}><span className="text-ink-3 line-through">{l.from}</span> → <span className="font-medium">{l.to}</span> <span className="text-ink-3 tabular-nums">· {l.n.toLocaleString()} rows</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}
