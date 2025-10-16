from pathlib import Path
from typing import List, Optional, Dict, Any
import os
from db import (
    upsert_script, list_scripts, update_meta, delete_script,
    list_all_folders, list_folders, create_folder, rename_folder, delete_folder,
    move_folder, assign_script_folder, list_scripts_in_folder,
)

PY_EXTS = {".py"}

def import_file(path: Path, name: Optional[str] = None, tags: str = "", description: str = "", folder_id: Optional[int] = None) -> int:
    path = path.expanduser().resolve()
    if not path.exists() or path.suffix.lower() not in PY_EXTS:
        raise ValueError("Pythonスクリプト(.py)のみ対応です")
    # Default to filename WITH extension (e.g., my_script.py)
    sid = upsert_script(name or path.name, str(path), tags, description)
    if folder_id is not None:
        try:
            assign_script_folder(sid, int(folder_id))
        except Exception:
            pass
    return sid

def import_directory(dir_path: Path, recurse: bool = True, folder_id: Optional[int] = None) -> int:
    dir_path = dir_path.expanduser().resolve()
    if not dir_path.is_dir():
        raise ValueError("ディレクトリを指定してください")
    count = 0
    walker = dir_path.rglob("*.py") if recurse else dir_path.glob("*.py")
    for p in walker:
        try:
            # Store filename with extension for clarity in the list view
            sid = upsert_script(p.name, str(p), "", "")
            if folder_id is not None:
                try:
                    assign_script_folder(sid, int(folder_id))
                except Exception:
                    pass
            count += 1
        except Exception:
            pass
    return count

def fetch_all() -> List[Dict[str, Any]]:
    rows = list_scripts()
    return [
        dict(id=r[0], name=r[1], path=r[2], tags=r[3], description=r[4], last_run=r[5], run_count=r[6], folder_id=r[7])
        for r in rows
    ]

def save_meta(sid: int, name: str, tags: str, description: str):
    update_meta(sid, name, tags, description)

def remove(sid: int):
    delete_script(sid)

# ----- Folder APIs (re-export db functions) -----
def folders_all():
    return list_all_folders()

def folders(parent_id: Optional[int] = None):
    return list_folders(parent_id)

def folder_create(name: str, parent_id: Optional[int] = None) -> int:
    return create_folder(name, parent_id)

def folder_rename(folder_id: int, name: str):
    rename_folder(folder_id, name)

def folder_delete(folder_id: int):
    delete_folder(folder_id)

def folder_move(folder_id: int, new_parent_id: Optional[int]):
    move_folder(folder_id, new_parent_id)

def assign_script_to_folder(script_id: int, folder_id: Optional[int]):
    assign_script_folder(script_id, folder_id)

def scripts_in_folder(folder_id: Optional[int]):
    return list_scripts_in_folder(folder_id)
