"""Tiny persistence layer: a key-value table for app state + analysis tables rebuilt from it.
State (source of truth): rfx, extractions (per vendor), overrides, settings, event log.
Analysis tables (derived, rebuilt on every change): lines, vendors, quotes, flags, questionnaire,
terms, alternates, documents. The analyst agent only ever reads the derived tables."""
import json, sqlite3, time
from contextlib import contextmanager
from .config import DB_PATH

def _conn(readonly=False):
    if readonly:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        c.execute("PRAGMA query_only = ON")
    else:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
    return c

@contextmanager
def conn(readonly=False):
    c = _conn(readonly)
    try:
        yield c
        if not readonly:
            c.commit()
    finally:
        c.close()

def init():
    with conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, updated REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS event_log (ts REAL, actor TEXT, event TEXT, detail TEXT)")

def put(k, v):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (k, json.dumps(v, ensure_ascii=False, default=str), time.time()))

def get(k, default=None):
    with conn() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default

def delete_prefix(prefix):
    with conn() as c:
        c.execute("DELETE FROM kv WHERE k LIKE ?", (prefix + "%",))

def keys(prefix=""):
    with conn() as c:
        return [r[0] for r in c.execute("SELECT k FROM kv WHERE k LIKE ?", (prefix + "%",))]

def log(event, detail="", actor="system"):
    with conn() as c:
        c.execute("INSERT INTO event_log VALUES (?,?,?,?)", (time.time(), actor, event, json.dumps(detail, default=str) if not isinstance(detail, str) else detail))

def events(limit=200):
    with conn() as c:
        return c.execute("SELECT ts, actor, event, detail FROM event_log ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()

def write_tables(tables: dict):
    """tables: name -> pandas.DataFrame. Replaces each table atomically."""
    with conn() as c:
        for name, df in tables.items():
            df.to_sql(name, c, if_exists="replace", index=False)

def read_sql(sql, params=()):
    import pandas as pd
    with conn(readonly=True) as c:
        return pd.read_sql_query(sql, c, params=params)

def reset_all():
    import shutil
    from .config import INBOX_DIR
    with conn() as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            c.execute(f'DROP TABLE IF EXISTS "{t}"')
    shutil.rmtree(INBOX_DIR, ignore_errors=True); INBOX_DIR.mkdir(parents=True, exist_ok=True)
    init()
