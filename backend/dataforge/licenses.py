"""License detection & classification (plan §3.3, §9)."""
from __future__ import annotations

import re

# class → (commercial, redistribution, attribution_required, share_alike)
CLASSES = {
    "commercial_ok": "Commercial OK",
    "copyleft": "Commercial OK (share-alike)",
    "non_commercial": "Non-commercial",
    "research_only": "Research only",
    "no_derivatives": "No derivatives",
    "local_only": "Local use only",
    "unknown": "Unknown",
}

_RULES: list[tuple[str, str, dict]] = [
    # pattern, class, rights
    (r"\bcc[- ]?0\b|public[- ]?domain|\bpddl\b|\bunlicense\b|\bcc0-1\.0", "commercial_ok",
     {"commercial": True, "redistribution": True, "attribution": False, "share_alike": False}),
    (r"cc[- ]?by[- ]?nc|non[- ]?commercial|noncommercial", "non_commercial",
     {"commercial": False, "redistribution": True, "attribution": True, "share_alike": None}),
    (r"cc[- ]?by[- ]?(4|3|2)?\.?0?[- ]?sa|cc-by-sa|share[- ]?alike|\bgpl|\bagpl|\blgpl|\bodbl|\bmpl", "copyleft",
     {"commercial": True, "redistribution": True, "attribution": True, "share_alike": True}),
    (r"cc[- ]?by[- ]?nd|no[- ]?deriv", "no_derivatives",
     {"commercial": True, "redistribution": True, "attribution": True, "share_alike": None}),
    (r"\bcc[- ]?by\b|cc-by-4\.0|cc-by-3\.0|\bmit\b|apache|\bbsd|\bisc\b|\bzlib\b|\bodc[- ]?by\b|openrail|llama\s?\d|bigscience|\bcdla[- ]?permissive|\bafl|\bartistic|\becl|\bwtfpl|\bpostgresql",
     "commercial_ok", {"commercial": True, "redistribution": True, "attribution": True, "share_alike": False}),
    (r"research[- ]?only|academic|c-uda|cdla[- ]?sharing|other[- ]?research|for research", "research_only",
     {"commercial": False, "redistribution": False, "attribution": True, "share_alike": None}),
    (r"youtube|standard youtube license|all rights reserved|copyright", "local_only",
     {"commercial": False, "redistribution": False, "attribution": True, "share_alike": None}),
]

UNKNOWN_RIGHTS = {"commercial": None, "redistribution": None, "attribution": None, "share_alike": None}


def classify(license_str: str | None) -> tuple[str, dict]:
    if not license_str:
        return "unknown", dict(UNKNOWN_RIGHTS)
    s = license_str.lower().strip()
    if s in {"other", "unknown", "none", "noassertion", "unspecified", "not specified"}:
        return "unknown", dict(UNKNOWN_RIGHTS)
    for pat, cls, rights in _RULES:
        if re.search(pat, s):
            return cls, dict(rights)
    return "unknown", dict(UNKNOWN_RIGHTS)


def color(cls: str) -> str:
    """Plan §7: green = usable, amber = check license, red = blocked."""
    return {"commercial_ok": "green", "copyleft": "green", "non_commercial": "amber",
            "research_only": "amber", "no_derivatives": "amber", "unknown": "amber", "local_only": "red"}.get(cls, "amber")


def compatible(classes: list[str], intended_use: str = "research") -> tuple[bool, list[str]]:
    """Check whether a set of source licenses can be merged into one exported dataset."""
    problems: list[str] = []
    if intended_use == "commercial":
        bad = [c for c in classes if c in {"non_commercial", "research_only", "local_only"}]
        if bad:
            problems.append(f"{len(bad)} source(s) forbid commercial use")
    if "local_only" in classes:
        problems.append("contains local-only media (e.g. YouTube) — not redistributable; export for local training only")
    if "no_derivatives" in classes:
        problems.append("contains no-derivatives sources - transformed/merged output may not be shared")
    if "copyleft" in classes and "non_commercial" in classes:
        problems.append("mixes share-alike and non-commercial terms — combined licence is ambiguous")
    if "unknown" in classes:
        problems.append(f"{classes.count('unknown')} source(s) have unknown licences — verify before sharing")
    blocking = intended_use == "commercial" and any(c in {"non_commercial", "research_only", "local_only"} for c in classes)
    return (not blocking), problems
