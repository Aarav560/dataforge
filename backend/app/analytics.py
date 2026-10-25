"""Version statistics and diffs, computed with DuckDB straight from Parquet."""

from pathlib import Path

import duckdb

TOP_K = 20
HIST_BINS = 10
CATEGORICAL_MAX = 50
# Bookkeeping columns whose skew is expected (80/10/10 splits, review flags).
NOT_CLASSES = {"split", "needs_review", "label_source", "label_confidence", "source_file", "chunk", "video", "game"}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET threads TO 2")
    return con


def compute_stats(path: Path) -> dict:
    con = _connect()
    try:
        src = f"read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
        total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        columns = []
        for name, dtype, *_ in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall():
            col = {"name": name, "type": dtype}
            q = _q(name)
            if dtype.endswith("[]") or dtype.startswith(("STRUCT", "MAP")):
                col["kind"] = "nested"
                col["nulls"] = con.execute(f"SELECT count(*) - count({q}) FROM {src}").fetchone()[0]
                columns.append(col)
                continue
            nulls, distinct = con.execute(f"SELECT count(*) - count({q}), approx_count_distinct({q}) FROM {src}").fetchone()
            col.update(nulls=nulls, distinct=distinct)
            numeric = any(dtype.startswith(t) for t in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT",
                                                         "DOUBLE", "DECIMAL", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"))
            if numeric:
                lo, hi, mean, std = con.execute(
                    f"SELECT min({q}), max({q}), avg({q}), stddev_samp({q}) FROM {src}"
                ).fetchone()
                col.update(kind="numeric", min=lo, max=hi, mean=mean, std=std)
                if lo is not None and hi is not None and hi > lo and distinct > CATEGORICAL_MAX:
                    width = (hi - lo) / HIST_BINS
                    rows = con.execute(
                        f"SELECT least(floor(({q} - {lo}) / {width}), {HIST_BINS - 1})::INT b, count(*) "
                        f"FROM {src} WHERE {q} IS NOT NULL GROUP BY b ORDER BY b"
                    ).fetchall()
                    counts = dict(rows)
                    col["histogram"] = [
                        {"from": lo + i * width, "to": lo + (i + 1) * width, "count": counts.get(i, 0)}
                        for i in range(HIST_BINS)
                    ]
            elif dtype == "BOOLEAN":
                col["kind"] = "categorical"
            else:
                # Few distinct values that actually repeat; all-unique strings are text.
                repeats = distinct <= max(1, 0.5 * (total - nulls))
                col["kind"] = "categorical" if distinct <= CATEGORICAL_MAX and repeats else "text"
                if col["kind"] == "text":
                    avg_len = con.execute(f"SELECT avg(length({q}::VARCHAR)) FROM {src}").fetchone()[0]
                    col["avg_length"] = avg_len
            if col["kind"] == "categorical" or (numeric and distinct <= CATEGORICAL_MAX):
                top = con.execute(
                    f"SELECT {q}::VARCHAR v, count(*) c FROM {src} GROUP BY v ORDER BY c DESC LIMIT {TOP_K}"
                ).fetchall()
                col["top"] = [{"value": v, "count": c} for v, c in top]
            columns.append(col)
        return {"rows": total, "columns": columns}
    finally:
        con.close()


def class_imbalance(stats: dict) -> list[dict]:
    """Categorical columns whose most common value dominates the least common one."""
    out = []
    for col in stats.get("columns", []):
        top = col.get("top") or []
        if col.get("kind") != "categorical" or col["name"] in NOT_CLASSES or col["type"] == "BOOLEAN":
            continue
        if len(top) < 2 or len(top) >= TOP_K:
            continue
        hi, lo = top[0]["count"], top[-1]["count"]
        if lo and hi / lo >= 5:
            out.append({"column": col["name"], "ratio": round(hi / lo, 1), "most": top[0]["value"], "least": top[-1]["value"]})
    return out


def diff(a: Path, b: Path, sample: int = 5) -> dict:
    """Compare version A (older) with version B (newer)."""
    con = _connect()
    try:
        sa = f"read_parquet('{str(a).replace(chr(39), chr(39) * 2)}')"
        sb = f"read_parquet('{str(b).replace(chr(39), chr(39) * 2)}')"
        cols_a = {n: t for n, t, *_ in con.execute(f"DESCRIBE SELECT * FROM {sa}").fetchall()}
        cols_b = {n: t for n, t, *_ in con.execute(f"DESCRIBE SELECT * FROM {sb}").fetchall()}
        common = [c for c in cols_a if c in cols_b and cols_a[c] == cols_b[c]]
        rows_a = con.execute(f"SELECT count(*) FROM {sa}").fetchone()[0]
        rows_b = con.execute(f"SELECT count(*) FROM {sb}").fetchone()[0]
        result = {
            "rows": {"before": rows_a, "after": rows_b, "delta": rows_b - rows_a},
            "columns": {
                "added": [{"name": c, "type": cols_b[c]} for c in cols_b if c not in cols_a],
                "removed": [{"name": c, "type": cols_a[c]} for c in cols_a if c not in cols_b],
                "type_changed": [{"name": c, "before": cols_a[c], "after": cols_b[c]}
                                 for c in cols_a if c in cols_b and cols_a[c] != cols_b[c]],
            },
            "compared_columns": common,
        }
        if common:
            sel = ", ".join(_q(c) for c in common)
            added_q = f"SELECT {sel} FROM {sb} EXCEPT ALL SELECT {sel} FROM {sa}"
            removed_q = f"SELECT {sel} FROM {sa} EXCEPT ALL SELECT {sel} FROM {sb}"
            result["rows"]["added"] = con.execute(f"SELECT count(*) FROM ({added_q})").fetchone()[0]
            result["rows"]["removed"] = con.execute(f"SELECT count(*) FROM ({removed_q})").fetchone()[0]
            result["rows"]["unchanged"] = rows_b - result["rows"]["added"]

            def rows(q):
                cur = con.execute(f"{q} LIMIT {sample}")
                names = [d[0] for d in cur.description]
                return [dict(zip(names, r)) for r in cur.fetchall()]

            result["sample_added"] = rows(added_q)
            result["sample_removed"] = rows(removed_q)
        return result
    finally:
        con.close()
