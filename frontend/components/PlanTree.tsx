"use client";
import { useState } from "react";
import { KIND_COLOR, Kind, Need, Plan, SOURCE_LABEL } from "@/lib/api";

const KINDS: Kind[] = ["dataset", "model", "environment", "paper", "video", "image", "web", "code"];

/** Stage 01 — review and edit what DataForge will look for (plan §3.1). */
export function PlanView({ plan, onChange, onSearch, llmJudge, setLlmJudge }: {
  plan: Plan; onChange: (p: Plan) => void; onSearch: () => void; llmJudge: boolean; setLlmJudge: (b: boolean) => void;
}) {
  const [newKind, setNewKind] = useState<Kind>("dataset");
  const [newDesc, setNewDesc] = useState("");
  const setNeed = (id: string, patch: Partial<Need>) => onChange({ ...plan, needs: plan.needs.map((n) => (n.id === id ? { ...n, ...patch } : n)) });
  const enabled = plan.needs.filter((n) => n.enabled !== false);
  const sources = new Set(enabled.flatMap((n) => n.sources));
  const queries = enabled.reduce((a, n) => a + n.queries.length, 0);

  const addNeed = () => {
    if (!newDesc.trim()) return;
    onChange({ ...plan, needs: [...plan.needs, { id: "", kind: newKind, description: newDesc.trim(), queries: [plan.subject], sources: [], status: "pending", priority: 3 }] });
    setNewDesc("");
  };

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-6xl mx-auto px-6 py-8 grid lg:grid-cols-[320px_1fr] gap-8 items-start">
        <aside className="lg:sticky lg:top-8 space-y-4">
          <div>
            <p className="eyebrow">Approach</p>
            <h2 className="display text-[26px] font-semibold leading-tight mt-1 capitalize">{plan.approach.primary}</h2>
            <p className="text-[13.5px] text-ink-2 mt-2">{plan.approach.rationale}</p>
            {plan.approach.alternatives.length > 0 && (
              <div className="mt-3">
                <p className="eyebrow mb-1.5">Also consider</p>
                <div className="flex flex-wrap gap-1.5">{plan.approach.alternatives.map((a) => <span key={a} className="pill">{a}</span>)}</div>
              </div>
            )}
            <p className="text-[12px] text-ink-3 mt-3">Domain: {plan.domain} · planned by {plan.planner.startsWith("llm") ? "local LLM + rules" : "built-in rules"}</p>
          </div>
          {plan.synthetic.recommended && (
            <div className="rounded-xl bg-heat-soft p-4 text-[13px]">
              <p className="font-semibold text-heat">Simulation fits this goal</p>
              <p className="text-ink-2 mt-1">{plan.synthetic.rationale}</p>
              <p className="text-ink-3 mt-2 text-[12px]">Generate it in stage 05 — Synthesize.</p>
            </div>
          )}
          <div className="card p-4 space-y-3">
            <div className="grid grid-cols-3 text-center">
              <Figure n={enabled.length} label="needs" />
              <Figure n={sources.size} label="sources" />
              <Figure n={queries} label="queries" />
            </div>
            <label className="flex items-start gap-2 text-[12.5px] text-ink-2">
              <input type="checkbox" checked={llmJudge} onChange={() => setLlmJudge(!llmJudge)} className="mt-0.5 accent-[var(--accent)]" />
              <span>Double-check the top results with the local LLM <span className="text-ink-3">(slower, more precise)</span></span>
            </label>
            <button className="btn btn-primary w-full h-10 justify-center text-[14px]" onClick={onSearch} disabled={!enabled.length}>
              Search {sources.size} sources →
            </button>
          </div>
        </aside>

        <section>
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="display text-[18px] font-semibold">What to look for</h3>
            <span className="text-[12.5px] text-ink-3">Edit queries, switch needs off, or add your own.</span>
          </div>
          <ul className="space-y-2.5">
            {plan.needs.map((n, i) => {
              const on = n.enabled !== false;
              return (
                <li key={n.id || i} className={`card p-4 arrive transition-opacity ${on ? "" : "opacity-55"}`} style={{ animationDelay: `${i * 30}ms` }}>
                  <div className="flex items-start gap-3">
                    <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: KIND_COLOR[n.kind] }} aria-hidden />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="mono text-[11px] uppercase tracking-wide" style={{ color: KIND_COLOR[n.kind] }}>{n.kind}</span>
                        <NeedStatus s={n.status} count={n.count} />
                      </div>
                      <input value={n.description} onChange={(e) => setNeed(n.id, { description: e.target.value })} aria-label="Need description"
                        className="w-full bg-transparent text-[15px] font-medium outline-none rounded focus:bg-sunken -mx-1 px-1 py-0.5 mt-0.5" />
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {n.queries.map((q, qi) => (
                          <span key={qi} className="pill !h-6 !text-[12px] !text-ink">
                            {q}
                            <button className="text-ink-3 hover:text-bad -mr-1 px-0.5" aria-label={`Remove query ${q}`}
                              onClick={() => setNeed(n.id, { queries: n.queries.filter((_, j) => j !== qi) })}>×</button>
                          </span>
                        ))}
                        <input placeholder="Add query ↵" aria-label="Add query"
                          className="h-6 w-28 bg-transparent text-[12px] outline-none border-b border-dashed border-line-2 focus:border-accent"
                          onKeyDown={(e) => {
                            const el = e.target as HTMLInputElement;
                            if (e.key === "Enter" && el.value.trim()) { setNeed(n.id, { queries: [...n.queries, el.value.trim()] }); el.value = ""; }
                          }} />
                      </div>
                      <p className="mt-2 text-[12px] text-ink-3">
                        Searches {[...new Set(n.sources.map((s) => SOURCE_LABEL[s] ?? s))].join(", ") || "default sources"}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <Switch on={on} onChange={() => setNeed(n.id, { enabled: !on })} label={`Include ${n.description}`} />
                      <button className="btn btn-ghost btn-sm" aria-label={`Remove ${n.description}`}
                        onClick={() => onChange({ ...plan, needs: plan.needs.filter((x) => x.id !== n.id) })}>Remove</button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="mt-3 card p-3 flex flex-wrap items-center gap-2 border-dashed">
            <select className="input" value={newKind} onChange={(e) => setNewKind(e.target.value as Kind)} aria-label="Kind of data">
              {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <input className="input flex-1 min-w-48" placeholder="Describe another data need, e.g. “pro tournament replays”" value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addNeed()} />
            <button className="btn" onClick={addNeed} disabled={!newDesc.trim()}>Add need</button>
          </div>
        </section>
      </div>
    </div>
  );
}

function Figure({ n, label }: { n: number; label: string }) {
  return <div><div className="display text-[24px] font-semibold tabular-nums">{n}</div><div className="text-[11.5px] text-ink-3">{label}</div></div>;
}

function Switch({ on, onChange, label }: { on: boolean; onChange: () => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={onChange}
      className={`relative w-9 h-5 rounded-full transition-colors ${on ? "bg-accent" : "bg-line-2"}`}>
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-[left] ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function NeedStatus({ s, count }: { s: string; count?: number }) {
  if (s === "searching") return <span className="pill pill-heat"><span className="ember !w-1.5 !h-1.5" aria-hidden />searching</span>;
  if (s === "done") return <span className="pill pill-ok">{count != null ? `${count} found` : "searched"}</span>;
  return null;
}
