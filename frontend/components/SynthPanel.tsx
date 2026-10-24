"use client";
import { useEffect, useState } from "react";
import { api, Plan } from "@/lib/api";

const PARAM_LABEL: Record<string, string> = {
  rows_target: "Rows", agent: "Agent", epsilon: "Exploration (ε)", max_digits: "Max digits", size: "Maze size", seed: "Seed",
};

/** Stage 05 — when simulation beats scraping, generate the data here or start from a script (plan §3.4). */
export function SynthPanel({ pid, plan, onStarted }: { pid: string; plan: Plan; onStarted: () => void }) {
  const s = plan.synthetic;
  const [gens, setGens] = useState<Record<string, { label: string; schema: string; params: Record<string, unknown> }>>({});
  const [gen, setGen] = useState<string>(s.generator ?? "");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { api.generators().then(setGens); }, []);
  useEffect(() => { if (gen && gens[gen]) setParams({ ...gens[gen].params }); }, [gen, gens]);

  const run = async () => {
    setMsg(null);
    try {
      await api.synthesize(pid, gen, params);
      setMsg({ ok: true, text: "Generating. The result becomes a new dataset version when it finishes." });
      onStarted();
    } catch (e) { setMsg({ ok: false, text: `Generation didn't start: ${e instanceof Error ? e.message : e}` }); }
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
      <section className="grid md:grid-cols-[1fr_auto] gap-6 items-center">
        <div>
          <p className="eyebrow">Simulation fit</p>
          <h2 className="display text-[28px] font-semibold mt-1">
            {s.recommended ? "Generate this data instead of collecting it" : "Real data is likely the better source"}
          </h2>
          <p className="text-ink-2 mt-2 max-w-[62ch]">{s.rationale}</p>
        </div>
        <Gauge value={s.score} hot={s.recommended} />
      </section>

      <section className="card p-5">
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="text-[12px] text-ink-3">Generator</span>
            <select className="input block mt-1 min-w-60" value={gen} onChange={(e) => { setGen(e.target.value); if (!e.target.value) setParams({}); }}>
              <option value="">Choose a generator…</option>
              {Object.entries(gens).map(([k, g]) => <option key={k} value={k}>{g.label}{k === s.generator ? " — suggested" : ""}</option>)}
            </select>
          </label>
          {Object.entries(params).map(([k, v]) => (
            <label key={k} className="block">
              <span className="text-[12px] text-ink-3">{PARAM_LABEL[k] ?? k}</span>
              <input className="input block mt-1 w-32 tabular-nums" value={String(v)}
                onChange={(e) => setParams({ ...params, [k]: typeof v === "number" ? Number(e.target.value) : e.target.value })} />
            </label>
          ))}
          <button className="btn btn-primary h-8 ml-auto" disabled={!gen} onClick={run}>Generate dataset</button>
        </div>
        {gen && gens[gen] && <p className="mt-4 text-[12.5px] text-ink-3">Columns: <span className="mono text-ink-2">{gens[gen].schema}</span></p>}
        {msg && <p className={`mt-3 text-[13px] ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.text}</p>}
      </section>

      {s.scaffold && (
        <section>
          <div className="flex items-center gap-3 mb-2">
            <h3 className="font-semibold">Starter script</h3>
            <span className="text-[12.5px] text-ink-3">Run it yourself to collect rollouts from a full simulator.</span>
            <button className="btn btn-sm ml-auto" onClick={() => { navigator.clipboard.writeText(s.scaffold!); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="mono text-[12px] leading-relaxed bg-surface border border-line rounded-xl p-4 overflow-auto">{s.scaffold}</pre>
        </section>
      )}
    </div>
  );
}

function Gauge({ value, hot }: { value: number; hot: boolean }) {
  const r = 34, c = 2 * Math.PI * r;
  return (
    <div className="relative w-24 h-24" role="img" aria-label={`Simulation fit ${Math.round(value * 100)} percent`}>
      <svg viewBox="0 0 80 80" className="w-full h-full -rotate-90">
        <circle cx="40" cy="40" r={r} fill="none" stroke="var(--line)" strokeWidth="6" />
        <circle cx="40" cy="40" r={r} fill="none" stroke={hot ? "var(--heat)" : "var(--ink-3)"} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${c * value} ${c}`} />
      </svg>
      <span className="absolute inset-0 grid place-items-center display text-[22px] font-semibold tabular-nums">{Math.round(value * 100)}</span>
    </div>
  );
}
