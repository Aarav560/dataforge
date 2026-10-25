import type { LicenseClass } from "@/lib/api";

// green = usable, amber = check license, red = blocked for commercial use.
const STYLES: Record<LicenseClass, { label: string; cls: string }> = {
  commercial: { label: "commercial OK", cls: "bg-green-500/15 text-green-500 border-green-500/40" },
  non_commercial: { label: "non-commercial", cls: "bg-red-500/15 text-red-400 border-red-500/40" },
  research_only: { label: "research only", cls: "bg-red-500/15 text-red-400 border-red-500/40" },
  local_only: { label: "local use only", cls: "bg-red-500/15 text-red-400 border-red-500/40" },
  unknown: { label: "check license", cls: "bg-amber-500/15 text-amber-500 border-amber-500/40" },
};

export function LicenseBadge({ cls, license }: { cls: LicenseClass; license: string | null }) {
  const s = STYLES[cls];
  return (
    <span
      title={license ?? "no license declared"}
      className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium ${s.cls}`}
    >
      {s.label}
      {license ? <span className="mono opacity-70"> · {license.length > 24 ? license.slice(0, 24) + "…" : license}</span> : null}
    </span>
  );
}
