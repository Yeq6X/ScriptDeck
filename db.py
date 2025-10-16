from pathlib import Path
import sqlite3
from typing import Iterable, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import json

DB_PATH = Path.home() / ".scriptdeck" / "scriptdeck.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DDL = """
CREATE TABLE IF NOT EXISTS scripts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  tags TEXT DEFAULT '',
  description TEXT DEFAULT '',
  last_run TEXT DEFAULT NULL,
  run_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS venvs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  python_path TEXT NOT NULL,
  created_at TEXT DEFAULT NULL,
  last_used_at TEXT DEFAULT NULL
);
"""

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(DDL)
    _ensure_schema(conn)
    return conn

def _ensure_schema(conn: sqlite3.Connection):
    # Add new columns to scripts table if missing
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scripts)").fetchall()}
    if 'args_schema' not in cols:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN args_schema TEXT DEFAULT NULL")
        except Exception:
            pass
    if 'args_values' not in cols:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN args_values TEXT DEFAULT NULL")
        except Exception:
            pass
    if 'venv_id' not in cols:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN venv_id INTEGER DEFAULT NULL")
        except Exception:
            pass

def upsert_script(name: str, path: str, tags: str = "", description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO scripts(name, path, tags, description)
            VALUES(?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              name=excluded.name,
              tags=excluded.tags,
              description=excluded.description
            """,
            (name, path, tags, description),
        )
        return cur.lastrowid or conn.execute("SELECT id FROM scripts WHERE path=?", (path,)).fetchone()[0]

def list_scripts() -> Iterable[Tuple]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, path, tags, description, last_run, run_count FROM scripts ORDER BY id DESC"
        ).fetchall()

def update_meta(sid: int, name: str, tags: str, description: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scripts SET name=?, tags=?, description=? WHERE id=?",
            (name, tags, description, sid),
        )

def bump_run(sid: int, run_time_iso: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scripts SET last_run=?, run_count=COALESCE(run_count,0)+1 WHERE id=?",
            (run_time_iso, sid),
        )

def delete_script(sid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM scripts WHERE id=?", (sid,))

# ----- Script extras (args schema/values, venv) -----
def get_script_extras(sid: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT args_schema, args_values, venv_id FROM scripts WHERE id=?",
            (sid,),
        ).fetchone()
        if not row:
            return {"args_schema": None, "args_values": None, "venv_id": None}
        return {"args_schema": row[0], "args_values": row[1], "venv_id": row[2]}

def update_args_schema(sid: int, schema_json: Optional[str]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET args_schema=? WHERE id=?", (schema_json, sid))

def update_args_values(sid: int, values_json: Optional[str]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET args_values=? WHERE id=?", (values_json, sid))

def set_script_venv(sid: int, venv_id: Optional[int]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET venv_id=? WHERE id=?", (venv_id, sid))

# ----- Venvs CRUD -----
def upsert_venv(name: str, path: str, python_path: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO venvs(name, path, python_path, created_at, last_used_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              name=excluded.name,
              python_path=excluded.python_path
            """,
            (name, path, python_path, now, now),
        )
        return cur.lastrowid or conn.execute("SELECT id FROM venvs WHERE path=?", (path,)).fetchone()[0]

def list_venvs() -> Iterable[Tuple]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, path, python_path, created_at, last_used_at FROM venvs ORDER BY id DESC"
        ).fetchall()

def get_venv(venv_id: int) -> Optional[Tuple]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, path, python_path, created_at, last_used_at FROM venvs WHERE id=?",
            (venv_id,),
        ).fetchone()

def delete_venv(venv_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM venvs WHERE id=?", (venv_id,))

def touch_venv_last_used(venv_id: int):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE venvs SET last_used_at=? WHERE id=?", (now, venv_id))
