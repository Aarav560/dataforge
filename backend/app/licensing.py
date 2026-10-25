"""Map free-form license strings to usage classes.

Classes: commercial (OK for commercial use), non_commercial, research_only,
local_only (may be used locally but not redistributed, e.g. YouTube's
standard license), unknown.
"""

import re

COMMERCIAL = "commercial"
NON_COMMERCIAL = "non_commercial"
RESEARCH_ONLY = "research_only"
LOCAL_ONLY = "local_only"
UNKNOWN = "unknown"

_COMMERCIAL_IDS = {
    "mit", "apache-2.0", "apache 2.0", "bsd", "bsd-2-clause", "bsd-3-clause", "bsd-3-clause-clear",
    "isc", "unlicense", "cc0-1.0", "cc0", "cc-by-4.0", "cc-by-3.0", "cc-by-2.0", "cc-by-sa-4.0",
    "cc-by-sa-3.0", "odc-by", "odbl", "pddl", "mpl-2.0", "lgpl-3.0", "lgpl-2.1", "gpl-3.0",
    "gpl-2.0", "agpl-3.0", "wtfpl", "zlib", "bsl-1.0", "artistic-2.0", "openrail", "openrail++",
    "creativeml-openrail-m", "bigscience-openrail-m", "llama2", "llama3", "llama3.1", "llama3.2",
    "llama3.3", "gemma", "cdla-permissive-2.0", "cdla-permissive-1.0", "cdla-sharing-1.0",
    "ecl-2.0", "epl-2.0", "afl-3.0", "c-uda", "etalab-2.0", "ogl-uk-3.0",
}

_RESEARCH_HINTS = ("research only", "research-only", "academic", "research purposes", "other-research")


def classify_license(license_: str | None) -> str:
    if not license_:
        return UNKNOWN
    raw = license_.strip().lower()
    if raw in {"unknown", "other", "none", "noassertion", ""}:
        return UNKNOWN
    if "youtube standard" in raw:
        return LOCAL_ONLY
    if any(h in raw for h in _RESEARCH_HINTS):
        return RESEARCH_ONLY
    # NC anywhere in a Creative Commons string, or spelled out.
    if re.search(r"(^|[-\s(])nc([-\s)]|$)", raw) or "noncommercial" in raw or "non-commercial" in raw:
        return NON_COMMERCIAL
    if raw in _COMMERCIAL_IDS:
        return COMMERCIAL
    # Long-form names, e.g. Kaggle's "Attribution 4.0 International (CC BY 4.0)".
    normalized = re.sub(r"[\s_]+", "-", raw)
    if any(tok in normalized for tok in ("cc-by", "cc0", "public-domain", "odbl", "apache", "mit-license")):
        return COMMERCIAL
    if raw.startswith(("gpl", "gnu", "mit", "bsd", "apache", "database: open database")):
        return COMMERCIAL
    return UNKNOWN


def license_rights(license_: str | None) -> dict[str, bool | None]:
    """Usage rights for the license matrix.

    True = allowed, False = not allowed, None = unknown (check the terms).
    `attribution` and `share_alike` are obligations rather than permissions.
    """
    cls = classify_license(license_)
    raw = (license_ or "").lower()
    normalized = re.sub(r"[\s_]+", "-", raw)
    if cls == UNKNOWN:
        return {"commercial": None, "redistribution": None, "attribution": None, "share_alike": None}
    if cls == LOCAL_ONLY:
        return {"commercial": False, "redistribution": False, "attribution": True, "share_alike": False}
    public_domain = any(t in normalized for t in ("cc0", "public-domain", "unlicense", "pddl", "wtfpl"))
    copyleft = any(t in normalized for t in ("-sa", "gpl", "odbl", "cdla-sharing", "share-alike", "sharealike"))
    if "agpl" in normalized or "lgpl" in normalized or normalized.startswith("gpl"):
        copyleft = True
    return {
        "commercial": cls == COMMERCIAL,
        # Research-only and NC licenses usually still allow sharing under the same terms.
        "redistribution": cls != RESEARCH_ONLY or None,
        "attribution": not public_domain,
        "share_alike": copyleft,
    }
