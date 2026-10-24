"use client";
import { Command } from "cmdk";
import { useEffect, useState } from "react";

export interface CommandAction { id: string; label: string; hint?: string; group: string; run: () => void }

/** Ctrl/⌘ K palette: type a new goal and press Enter, or run any command (plan §7.1). */
export function CommandBar({ actions, onGoal, busy }: { actions: CommandAction[]; onGoal: (goal: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const groups = [...new Set(actions.map((a) => a.group))];
  const close = () => { setValue(""); setOpen(false); };

  return (
    <>
      <button onClick={() => setOpen(true)} className="btn w-56 justify-start text-ink-3 font-normal hidden sm:inline-flex" aria-label="Open command menu">
        <span>Search commands…</span>
        <span className="ml-auto kbd">Ctrl K</span>
      </button>
      <button onClick={() => setOpen(true)} className="btn btn-ghost sm:hidden" aria-label="Open command menu">⌘</button>
      {open && (
        <div className="fixed inset-0 z-50 bg-ink/25 backdrop-blur-[2px] flex items-start justify-center pt-[14vh] px-4" onMouseDown={close}>
          <Command label="Command menu" className="w-full max-w-xl card !shadow-[var(--shadow-lg)] overflow-hidden drawer-in" onMouseDown={(e) => e.stopPropagation()}>
            <Command.Input autoFocus value={value} onValueChange={setValue}
              placeholder="Type a command, or a new goal to plan…" className="w-full px-4 h-12 bg-transparent outline-none border-b border-line text-[15px] placeholder:text-ink-3" />
            <Command.List className="max-h-[50vh] overflow-auto p-1.5">
              {value.trim().length > 2 && (
                <Command.Item value={`plan ${value}`} onSelect={() => { onGoal(value.trim()); close(); }} forceMount
                  className="flex items-center gap-2 px-3 py-2 rounded-md text-[13.5px] cursor-pointer data-[selected=true]:bg-accent-soft">
                  <span className="text-accent font-medium">Plan a new dataset:</span><span className="truncate">{value}</span>
                  {busy && <span className="ember ml-auto" aria-hidden />}
                </Command.Item>
              )}
              <Command.Empty className="px-3 py-2 text-[13px] text-ink-3">No matching command.</Command.Empty>
              {groups.map((g) => (
                <Command.Group key={g} heading={g} className="[&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[.06em] [&_[cmdk-group-heading]]:text-ink-3 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1">
                  {actions.filter((a) => a.group === g).map((a) => (
                    <Command.Item key={a.id} value={`${a.label} ${a.id}`} onSelect={() => { a.run(); close(); }}
                      className="flex items-center gap-3 px-3 py-2 rounded-md text-[13.5px] cursor-pointer data-[selected=true]:bg-sunken">
                      <span className="truncate">{a.label}</span>
                      {a.hint && <span className="ml-auto kbd">{a.hint}</span>}
                    </Command.Item>
                  ))}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </div>
      )}
    </>
  );
}
