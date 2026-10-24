"use client";
import { useState } from "react";
import { Connector, KIND_COLOR, Project } from "@/lib/api";

const EXAMPLES = ["Train a Tetris AI", "Detect plant diseases from leaf photos", "Predict customer churn", "Speech recognition for Nepali", "An agent that plays chess"];

// The worked example from the product plan, shown as what a plan actually looks like.
const SPECIMEN: { kind: string; need: string; where: string }[] = [
  { kind: "dataset", need: "Logged games", where: "Hugging Face · Kaggle · Zenodo" },
  { kind: "environment", need: "Tetris simulator", where: "Gymnasium · GitHub" },
  { kind: "video", need: "Expert gameplay footage", where: "YouTube · Internet Archive" },
  { kind: "model", need: "Pretrained agents", where: "Hugging Face · GitHub" },
  { kind: "paper", need: "RL approaches", where: "arXiv · OpenAlex" },
];

export function Landing({ projects, onOpen, onGoal, planning, useLlm, setUseLlm, health, backendDown, loading }: {
  projects: Project[]; onOpen: (id: string) => void; onGoal: (g: string) => void; planning: boolean; useLlm: boolean; setUseLlm: (b: boolean) => void;
  health: { llm: { model: string | null; available: boolean }; connectors: Connector[] } | null; backendDown: boolean; loading: boolean;
}) {
  const [goal, setGoal] = useState("");
  if (loading) return <div className="h-full grid place-items-center text-ink-2">Opening project…</div>;
  const nSources = health?.connectors.length ?? 17;

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-6xl mx-auto px-6 pt-14 pb-16">
        {backendDown && (
          <div role="alert" className="card p-4 mb-8 border-bad/40 bg-bad-soft text-bad text-sm">
            The DataForge server isn&apos;t responding at <span className="mono">{process.env.NEXT_PUBLIC_API ?? "http://localhost:8000"}</span>.
            Start it with <span className="mono">uvicorn dataforge.main:app --port 8000</span> from the <span className="mono">backend</span> folder.
          </div>
        )}

        <div className="grid lg:grid-cols-[1.25fr_1fr] gap-12 items-start">
          <section className="arrive">
            <p className="eyebrow mb-4">ML dataset research</p>
            <h1 className="display text-[44px] md:text-[56px] leading-[1.02] font-semibold">
              Say what you want to train.<br />
              <span className="text-ink-3">Get the data to train it.</span>
            </h1>
            <p className="mt-5 text-[15px] text-ink-2 max-w-[52ch]">
              DataForge works out which data your model needs, searches {nSources} free sources at once, checks every licence,
              and builds a versioned dataset you can export straight into training.
            </p>

            <form className="mt-8" onSubmit={(e) => { e.preventDefault(); if (goal.trim().length > 2) onGoal(goal.trim()); }}>
              <label htmlFor="goal" className="sr-only">What do you want to train?</label>
              <div className="card flex items-center gap-2 p-2 pl-4 focus-within:border-accent focus-within:shadow-[0_0_0_4px_var(--accent-soft)] transition-shadow">
                <input id="goal" autoFocus value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="I want to train a Tetris AI"
                  className="flex-1 bg-transparent outline-none text-[16px] py-2 placeholder:text-ink-3" />
                <button className="btn btn-primary h-10 px-5 text-[14px]" disabled={planning || goal.trim().length < 3}>
                  {planning ? <><span className="ember" aria-hidden /> Planning…</> : "Plan dataset"}
                </button>
              </div>
            </form>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {EXAMPLES.map((e) => (
                <button key={e} onClick={() => setGoal(e)} className="pill hover:border-ink-3 hover:text-ink transition-colors !h-7 !px-3 !text-[12.5px]">{e}</button>
              ))}
            </div>
            <label className="mt-5 flex items-center gap-2 text-[13px] text-ink-2 w-fit">
              <input type="checkbox" checked={useLlm} onChange={() => setUseLlm(!useLlm)} className="accent-[var(--accent)]" />
              Plan with the local LLM
              {health && (health.llm.available
                ? <span className="pill pill-ok">{health.llm.model}</span>
                : <span className="pill pill-warn">Ollama not running · built-in rules</span>)}
            </label>
          </section>

          <aside className="card p-5 arrive" style={{ animationDelay: "80ms" }} aria-label="Example plan">
            <div className="flex items-baseline justify-between">
              <p className="eyebrow">Example plan</p>
              <span className="pill pill-accent">reinforcement learning</span>
            </div>
            <p className="display text-[22px] font-semibold mt-2">“Train a Tetris AI”</p>
            <ul className="mt-4 divide-y divide-line">
              {SPECIMEN.map((s) => (
                <li key={s.need} className="py-2.5 flex items-center gap-3">
                  <span className="w-1.5 h-6 rounded-full shrink-0" style={{ background: KIND_COLOR[s.kind] }} aria-hidden />
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium">{s.need}</div>
                    <div className="text-[12px] text-ink-3">{s.where}</div>
                  </div>
                  <span className="ml-auto mono text-[11px] text-ink-3">{s.kind}</span>
                </li>
              ))}
            </ul>
            <div className="mt-3 rounded-lg bg-heat-soft text-heat px-3 py-2.5 text-[12.5px] leading-snug">
              <b>Better simulated than scraped:</b> a Tetris engine plays itself and logs millions of (state, action, reward) rows.
            </div>
          </aside>
        </div>

        {projects.length > 0 && (
          <section className="mt-16">
            <h2 className="eyebrow mb-3">Your projects</h2>
            <div className="card divide-y divide-line overflow-hidden">
              {projects.map((p) => (
                <button key={p.id} onClick={() => onOpen(p.id)} className="w-full flex items-center gap-4 px-4 py-3 text-left hover:bg-sunken transition-colors">
                  <span className="font-medium truncate">{p.goal}</span>
                  <span className="ml-auto flex items-center gap-4 text-[12.5px] text-ink-2 shrink-0">
                    <span><b className="text-ink tabular-nums">{(p.results ?? 0).toLocaleString()}</b> results</span>
                    <span className="hidden sm:inline"><b className="text-ink tabular-nums">{p.versions ?? 0}</b> versions</span>
                    <span className="hidden md:inline text-ink-3 w-24 text-right">{new Date(p.created_at * 1000).toLocaleDateString()}</span>
                    <span aria-hidden className="text-ink-3">→</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {health && (
          <section className="mt-12">
            <h2 className="eyebrow mb-3">Sources — all free, no paid APIs</h2>
            <div className="flex flex-wrap gap-1.5">
              {health.connectors.map((c) => (
                <span key={c.name} className="pill !h-7 !px-3 !text-[12.5px]" title={c.requires ? `Works without a key. Optional: ${c.requires}` : "No key needed"}>
                  {c.label}{c.requires ? <span className="text-ink-3">·key optional</span> : null}
                </span>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
