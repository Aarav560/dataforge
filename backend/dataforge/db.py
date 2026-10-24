"""SQLite persistence (local stand-in for Postgres). Schema mirrors plan §8."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, goal TEXT NOT NULL, plan_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS data_needs (
    id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, description TEXT, approach TEXT,
    queries_json TEXT, sources_json TEXT, status TEXT, position INTEGER
);
CREATE TABLE IF NOT EXISTS search_results (
    id TEXT PRIMARY KEY, project_id TEXT, need_id TEXT, source TEXT, kind TEXT, url TEXT,
    title TEXT, description TEXT, size_bytes INTEGER, format TEXT, license TEXT,
    license_class TEXT, relevance REAL, quality REAL, score REAL, meta_json TEXT,
    dup_of TEXT, selected INTEGER DEFAULT 0, created_at REAL
);
CREATE INDEX IF NOT EXISTS ix_results_project ON search_results(project_id);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, project_id TEXT, result_id TEXT, storage_path TEXT, checksum TEXT,
    size_bytes INTEGER, status TEXT, error TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY, project_id TEXT, template TEXT, graph_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS dataset_versions (
    id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, schema_json TEXT, row_count INTEGER,
    card_md TEXT, export_paths_json TEXT, stats_json TEXT, sources_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS search_cache (
    key TEXT PRIMARY KEY, source TEXT, query TEXT, results_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, status TEXT, progress REAL,
    message TEXT, result_json TEXT, created_at REAL, updated_at REAL
);
"""

JSON_COLS = {"plan_json", "queries_json", "sources_json", "meta_json", "graph_json", "schema_json",
             "export_paths_json", "stats_json", "results_json", "result_json"}


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
        return _conn


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _encode(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, bool):
        return int(v)
    return v


def _decode(row: sqlite3.Row) -> dict:
    out = {}
    for k in row.keys():
        v = row[k]
        if k in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
            k = k[:-5] if k.endswith("_json") else k
        out[k] = v
    return out


def insert(table: str, **fields: Any) -> dict:
    fields.setdefault("id", new_id())
    if "created_at" in _columns(table):
        fields.setdefault("created_at", time.time())
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with _lock:
        conn().execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})",
                       [_encode(v) for v in fields.values()])
    return fields


def update(table: str, id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn().execute(f"UPDATE {table} SET {sets} WHERE id = ?", [_encode(v) for v in fields.values()] + [id])


def get(table: str, id: str) -> dict | None:
    with _lock:
        row = conn().execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    return _decode(row) if row else None


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        rows = conn().execute(sql, tuple(params)).fetchall()
    return [_decode(r) for r in rows]


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with _lock:
        conn().execute(sql, tuple(params))


_col_cache: dict[str, set[str]] = {}


def _columns(table: str) -> set[str]:
    if table not in _col_cache:
        with _lock:
            _col_cache[table] = {r[1] for r in conn().execute(f"PRAGMA table_info({table})")}
    return _col_cache[table]
