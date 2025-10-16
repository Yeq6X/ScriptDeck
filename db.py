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

CREATE TABLE IF NOT EXISTS option_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  script_id INTEGER NOT NULL,
  option TEXT NOT NULL,
  value TEXT NOT NULL,
  last_used_at TEXT DEFAULT NULL,
  use_count INTEGER DEFAULT 1,
  UNIQUE(script_id, option, value)
);

CREATE TABLE IF NOT EXISTS ai_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  script_id INTEGER NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  created_at TEXT DEFAULT NULL
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
    if 'working_dir' not in cols:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN working_dir TEXT DEFAULT NULL")
        except Exception:
            pass
    # Folders table and scripts.folder_id
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          parent_id INTEGER NULL REFERENCES folders(id) ON DELETE CASCADE,
          position INTEGER NOT NULL DEFAULT 0,
          UNIQUE(parent_id, name)
        );
        """
    )
    # Add folder_id column for scripts if missing
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scripts)").fetchall()}
    if 'folder_id' not in cols:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN folder_id INTEGER DEFAULT NULL REFERENCES folders(id) ON DELETE SET NULL")
        except Exception:
            pass
    # Indexes
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id)")
    except Exception:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_folder ON scripts(folder_id)")
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
            "SELECT id, name, path, tags, description, last_run, run_count, folder_id FROM scripts ORDER BY id DESC"
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

# ----- Folders -----
def list_folders(parent_id: Optional[int] = None) -> Iterable[Tuple]:
    with get_conn() as conn:
        if parent_id is None:
            return conn.execute(
                "SELECT id, name, parent_id, position FROM folders WHERE parent_id IS NULL ORDER BY position, name"
            ).fetchall()
        else:
            return conn.execute(
                "SELECT id, name, parent_id, position FROM folders WHERE parent_id=? ORDER BY position, name",
                (parent_id,),
            ).fetchall()

def list_all_folders() -> Iterable[Tuple]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, parent_id, position FROM folders ORDER BY parent_id NULLS FIRST, position, name"
        ).fetchall()

def create_folder(name: str, parent_id: Optional[int] = None) -> int:
    with get_conn() as conn:
        # Determine next position within the parent
        if parent_id is None:
            row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE parent_id IS NULL").fetchone()
        else:
            row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE parent_id=?", (parent_id,)).fetchone()
        pos = row[0] if row else 0
        cur = conn.execute(
            "INSERT INTO folders(name, parent_id, position) VALUES(?,?,?)",
            (name, parent_id, pos),
        )
        return cur.lastrowid

def rename_folder(folder_id: int, name: str):
    with get_conn() as conn:
        conn.execute("UPDATE folders SET name=? WHERE id=?", (name, folder_id))

def delete_folder(folder_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))

def move_folder(folder_id: int, new_parent_id: Optional[int], new_position: Optional[int] = None):
    with get_conn() as conn:
        if new_position is None:
            if new_parent_id is None:
                row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE parent_id IS NULL").fetchone()
            else:
                row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE parent_id=?", (new_parent_id,)).fetchone()
            new_position = row[0] if row else 0
        conn.execute("UPDATE folders SET parent_id=?, position=? WHERE id=?", (new_parent_id, new_position, folder_id))

def assign_script_folder(script_id: int, folder_id: Optional[int]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET folder_id=? WHERE id=?", (folder_id, script_id))

def list_scripts_in_folder(folder_id: Optional[int]) -> Iterable[Tuple]:
    with get_conn() as conn:
        if folder_id is None:
            return conn.execute(
                "SELECT id, name, path, tags, description, last_run, run_count, folder_id FROM scripts WHERE folder_id IS NULL ORDER BY name"
            ).fetchall()
        else:
            return conn.execute(
                "SELECT id, name, path, tags, description, last_run, run_count, folder_id FROM scripts WHERE folder_id=? ORDER BY name",
                (folder_id,),
            ).fetchall()

# ----- Script extras (args schema/values, venv) -----
def get_script_extras(sid: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT args_schema, args_values, venv_id, working_dir FROM scripts WHERE id=?",
            (sid,),
        ).fetchone()
        if not row:
            return {"args_schema": None, "args_values": None, "venv_id": None, "working_dir": None}
        return {"args_schema": row[0], "args_values": row[1], "venv_id": row[2], "working_dir": row[3]}

def update_args_schema(sid: int, schema_json: Optional[str]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET args_schema=? WHERE id=?", (schema_json, sid))

def update_args_values(sid: int, values_json: Optional[str]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET args_values=? WHERE id=?", (values_json, sid))

def set_script_venv(sid: int, venv_id: Optional[int]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET venv_id=? WHERE id=?", (venv_id, sid))

def set_working_dir(sid: int, working_dir: Optional[str]):
    with get_conn() as conn:
        conn.execute("UPDATE scripts SET working_dir=? WHERE id=?", (working_dir, sid))

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

# ----- Option history -----
def upsert_option_history(script_id: int, option: str, value: str, keep: int = 20):
    if not value:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO option_history(script_id, option, value, last_used_at, use_count)
            VALUES(?,?,?,?,1)
            ON CONFLICT(script_id, option, value) DO UPDATE SET
              last_used_at=excluded.last_used_at,
              use_count=option_history.use_count+1
            """,
            (script_id, option, value, now),
        )
        # prune
        conn.execute(
            """
            DELETE FROM option_history
            WHERE script_id=? AND option=? AND id NOT IN (
              SELECT id FROM option_history
              WHERE script_id=? AND option=?
              ORDER BY last_used_at DESC, use_count DESC, id DESC
              LIMIT ?
            )
            """,
            (script_id, option, script_id, option, keep),
        )

def list_option_history(script_id: int, option: str, limit: int = 20) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT value FROM option_history WHERE script_id=? AND option=? ORDER BY last_used_at DESC, use_count DESC, id DESC LIMIT ?",
            (script_id, option, limit),
        ).fetchall()
    return [r[0] for r in rows]

def delete_option_history(script_id: int, option: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM option_history WHERE script_id=? AND option=? AND value=?",
            (script_id, option, value),
        )

# ----- AI Q&A history -----
def add_ai_history(script_id: int, question: str, answer: str, keep: int = 100):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_history(script_id, question, answer, created_at) VALUES(?,?,?,?)",
            (script_id, question, answer, now),
        )
        # prune old rows keeping the latest 'keep'
        conn.execute(
            """
            DELETE FROM ai_history
            WHERE script_id=? AND id NOT IN (
              SELECT id FROM ai_history WHERE script_id=? ORDER BY created_at DESC, id DESC LIMIT ?
            )
            """,
            (script_id, script_id, keep),
        )

def list_ai_history(script_id: int, limit: int = 100):
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, created_at, question, answer FROM ai_history WHERE script_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (script_id, limit),
        ).fetchall()

def delete_ai_history(entry_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_history WHERE id=?", (entry_id,))
