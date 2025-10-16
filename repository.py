from pathlib import Path
from typing import List, Optional, Dict, Any
import os
from db import upsert_script, list_scripts, update_meta, delete_script

PY_EXTS = {".py"}

def import_file(path: Path, name: Optional[str] = None, tags: str = "", description: str = "") -> int:
    path = path.expanduser().resolve()
    if not path.exists() or path.suffix.lower() not in PY_EXTS:
        raise ValueError("Pythonスクリプト(.py)のみ対応です")
    return upsert_script(name or path.stem, str(path), tags, description)

def import_directory(dir_path: Path, recurse: bool = True) -> int:
    dir_path = dir_path.expanduser().resolve()
    if not dir_path.is_dir():
        raise ValueError("ディレクトリを指定してください")
    count = 0
    walker = dir_path.rglob("*.py") if recurse else dir_path.glob("*.py")
    for p in walker:
        try:
            upsert_script(p.stem, str(p), "", "")
            count += 1
        except Exception:
            pass
    return count

def fetch_all() -> List[Dict[str, Any]]:
    rows = list_scripts()
    return [
        dict(id=r[0], name=r[1], path=r[2], tags=r[3], description=r[4], last_run=r[5], run_count=r[6])
        for r in rows
    ]

def save_meta(sid: int, name: str, tags: str, description: str):
    update_meta(sid, name, tags, description)

def remove(sid: int):
    delete_script(sid)
