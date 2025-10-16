from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QTextEdit, QDialogButtonBox,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QFileDialog, QTableWidget, QTableWidgetItem, QMessageBox,
    QRadioButton, QCompleter, QSplitter
)
from PyQt6.QtCore import Qt, QProcess, QTimer, pyqtSignal, QStringListModel, QEvent, QObject, QThread
from PyQt6.QtGui import QShortcut, QKeySequence
import sys
import os
from pathlib import Path
import json
from db import (
    list_venvs, upsert_venv, delete_venv, get_script_extras, update_args_schema,
    update_args_values, set_script_venv, get_venv, set_working_dir,
    list_option_history, upsert_option_history, delete_option_history
)


class _HistoryEventFilter(QObject):
    def __init__(self, sid: int, option_name: str, model: QStringListModel, parent=None):
        super().__init__(parent)
        self.sid = sid
        self.option_name = option_name
        self.model = model

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            try:
                key = event.key()
                mods = event.modifiers()
            except Exception:
                return False
            if key == Qt.Key.Key_Delete and (mods & Qt.KeyboardModifier.ShiftModifier):
                view = obj  # QAbstractItemView
                idx = view.currentIndex()
                if idx.isValid():
                    val = idx.data()
                    try:
                        delete_option_history(self.sid, self.option_name, val)
                    except Exception:
                        pass
                    lst = [s for s in self.model.stringList() if s != val]
                    self.model.setStringList(lst)
                    return True
        return False


class _AIWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_key: str, user_text: str, script_path: str):
        super().__init__()
        self.api_key = api_key
        self.user_text = user_text
        self.script_path = script_path

    def run(self):
        try:
            import urllib.request
            import urllib.error
            import json as _json
            # Read script content (limit)
            try:
                with open(self.script_path, 'r', encoding='utf-8', errors='ignore') as f:
                    script = f.read()
            except Exception as e:
                script = f"<読み込みエラー: {e}>"
            if len(script) > 100_000:
                script = script[:100_000] + "\n... (truncated)"

            messages = [
                {"role": "system", "content": "You are a helpful coding assistant. Answer in Japanese unless asked otherwise."},
                {"role": "user", "content": (
                    "ユーザー入力:\n" + self.user_text.strip() +
                    "\n\n選択中のスクリプトの内容（抜粋）:\n```python\n" + script + "\n```\n"
                )},
            ]
            payload = {
                "model": "gpt-5",
                "messages": messages,
            }
            req = urllib.request.Request(
                url="https://api.openai.com/v1/chat/completions",
                data=_json.dumps(payload).encode('utf-8'),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = _json.loads(resp.read().decode('utf-8', errors='ignore'))
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not content:
                content = _json.dumps(data, ensure_ascii=False, indent=2)
            self.finished.emit(content)
        except urllib.error.HTTPError as he:
            try:
                err = he.read().decode('utf-8', errors='ignore')
            except Exception:
                err = str(he)
            self.error.emit(f"HTTPError: {he.code}\n{err}")
        except Exception as e:
            self.error.emit(str(e))


class AIAssistantPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_sid: int | None = None
        self.current_path: str | None = None
        self._thread: QThread | None = None
        self._worker: _AIWorker | None = None

        root = QVBoxLayout(self)
        # Vertical splitter: top (input+send), bottom (output)
        self.split = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(self.split, 1)

        # Top (input area container) - splitter handle will appear above the send button
        topw = QWidget()
        top_layout = QVBoxLayout(topw)
        top_layout.addWidget(QLabel("AIへ相談（入力して送信またはCtrl+Enter）"))
        self.input = QTextEdit()
        self.input.setPlaceholderText("質問や指示を入力...")
        self.input.setAcceptRichText(False)
        top_layout.addWidget(self.input, 1)

        # Bottom (send + output container)
        bottomw = QWidget()
        bottom_layout = QVBoxLayout(bottomw)
        ctrl = QHBoxLayout()
        self.btn_send = QPushButton("送信")
        ctrl.addStretch(1)
        ctrl.addWidget(self.btn_send)
        bottom_layout.addLayout(ctrl)
        bottom_layout.addWidget(QLabel("回答"))
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        bottom_layout.addWidget(self.output, 1)

        self.split.addWidget(topw)
        self.split.addWidget(bottomw)
        # Initial sizes: input area ~3 lines by default (handle above the send button)
        fm = self.input.fontMetrics()
        top_h = int(fm.lineSpacing() * 3 + fm.height() + 16)
        self.split.setSizes([top_h, 300])

        self.btn_send.clicked.connect(self._on_send)
        sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc.activated.connect(self._on_send)
        sc2 = QShortcut(QKeySequence("Ctrl+Enter"), self)
        sc2.activated.connect(self._on_send)

    def set_script(self, sid: int, path: str):
        self.current_sid = sid
        self.current_path = path

    def _on_send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if not self.current_path:
            QMessageBox.information(self, "情報", "スクリプトが選択されていません")
            return
        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(self, "APIキー未設定", ".env または環境変数 OPENAI_API_KEY を設定してください")
            return
        # Disable while running
        self.btn_send.setEnabled(False)
        self.output.append("[送信中] OpenAIへ問い合わせ中...\n")
        # Start worker thread
        self._thread = QThread(self)
        self._worker = _AIWorker(api_key, text, self.current_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.error.connect(self._cleanup_worker)
        self._thread.start()

    def _on_finished(self, content: str):
        self.output.append("\n[回答]\n" + content + "\n")
        self.btn_send.setEnabled(True)

    def _on_error(self, msg: str):
        self.output.append("\n[エラー]\n" + msg + "\n")
        self.btn_send.setEnabled(True)

    def _cleanup_worker(self, *args):
        try:
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(1000)
        except Exception:
            pass
        self._thread = None
        self._worker = None

    def _get_api_key(self) -> str | None:
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        # try to load from .env in cwd
        env_path = Path.cwd() / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
                return os.environ.get("OPENAI_API_KEY")
            except Exception:
                return None
        return None

class MetaEditDialog(QDialog):
    def __init__(self, name: str, tags: str, description: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("メタデータ編集")
        self.name_edit = QLineEdit(name)
        self.tags_edit = QLineEdit(tags)
        self.desc_edit = QTextEdit(description)

        form = QFormLayout(self)
        form.addRow("名前", self.name_edit)
        form.addRow("タグ（カンマ区切り）", self.tags_edit)
        form.addRow("説明", self.desc_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def values(self):
        return self.name_edit.text().strip(), self.tags_edit.text().strip(), self.desc_edit.toPlainText().strip()


class VenvManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Python環境の管理")
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["名前", "パス", "Python"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("追加...")
        self.btn_remove = QPushButton("削除")
        self.btn_close = QPushButton("閉じる")
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_close.clicked.connect(self.accept)

        self._id_by_row = {}
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        self._id_by_row.clear()
        for row in list_venvs():
            ridx = self.table.rowCount()
            self.table.insertRow(ridx)
            self.table.setItem(ridx, 0, QTableWidgetItem(row[1]))
            self.table.setItem(ridx, 1, QTableWidgetItem(row[2]))
            self.table.setItem(ridx, 2, QTableWidgetItem(row[3]))
            self._id_by_row[ridx] = row[0]

    def _on_add(self):
        folder = QFileDialog.getExistingDirectory(self, "venvフォルダを選択")
        if not folder:
            return
        folder_path = Path(folder)
        if sys.platform.startswith("win"):
            py = folder_path / "Scripts" / "python.exe"
        else:
            py = folder_path / "bin" / "python"
        if not py.exists():
            cfg = folder_path / "pyvenv.cfg"
            if not cfg.exists():
                QMessageBox.warning(self, "警告", "選択したフォルダはvenvではありません (python実行ファイル/pyvenv.cfgが見つかりません)")
                return
        name = folder_path.name
        # Use folder name as default
        # Save
        vid = upsert_venv(name, str(folder_path), str(py))
        if vid:
            self.refresh()

    def _on_remove(self):
        row = self.table.currentRow()
        if row < 0:
            return
        vid = self._id_by_row.get(row)
        if not vid:
            return
        ret = QMessageBox.question(self, "確認", "選択した環境を削除しますか？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        delete_venv(int(vid))
        self.refresh()


class ScriptDetailsPanel(QWidget):
    runRequested = pyqtSignal()
    stopRequested = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_sid: int | None = None
        self.current_path: str | None = None
        self._option_widgets: dict[str, QWidget] = {}
        self._venv_items: list[tuple[int, str, str]] = []  # (id, name, python_path)
        self._history_models: dict[str, QStringListModel] = {}
        self._history_filters: dict[str, _HistoryEventFilter] = {}

        root = QVBoxLayout(self)

        # Environment row
        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("環境:"))
        self.env_combo = QComboBox()
        self.btn_manage_env = QPushButton("管理...")
        self.btn_probe = QPushButton("オプション再取得")
        env_row.addWidget(self.env_combo, 1)
        env_row.addWidget(self.btn_manage_env)
        env_row.addWidget(self.btn_probe)
        root.addLayout(env_row)

        # Working directory row
        wd_row = QHBoxLayout()
        wd_row.addWidget(QLabel("作業ディレクトリ:"))
        self.rb_cwd_script = QRadioButton("スクリプトのディレクトリ")
        self.rb_cwd_custom = QRadioButton("指定")
        self.cwd_edit = QLineEdit()
        self.cwd_browse = QPushButton("参照...")
        wd_row.addWidget(self.rb_cwd_script)
        wd_row.addWidget(self.rb_cwd_custom)
        wd_row.addWidget(self.cwd_edit, 1)
        wd_row.addWidget(self.cwd_browse)
        root.addLayout(wd_row)

        # Scrollable form for options
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.scroll.setWidget(self.form_host)
        root.addWidget(self.scroll, 1)

        # Execute row (placed at bottom of the panel)
        exec_row = QHBoxLayout()
        self.btn_run = QPushButton("実行")
        self.btn_stop = QPushButton("停止")
        exec_row.addWidget(self.btn_run)
        exec_row.addWidget(self.btn_stop)
        exec_row.addStretch(1)
        root.addLayout(exec_row)

        self.env_combo.currentIndexChanged.connect(self._on_env_changed)
        self.btn_manage_env.clicked.connect(self._on_manage_env)
        self.btn_probe.clicked.connect(self._on_probe)
        self.btn_run.clicked.connect(lambda: self.runRequested.emit())
        self.btn_stop.clicked.connect(lambda: self.stopRequested.emit())
        self.rb_cwd_script.toggled.connect(self._on_workdir_changed)
        self.rb_cwd_custom.toggled.connect(self._on_workdir_changed)
        self.cwd_edit.editingFinished.connect(self._on_workdir_changed)
        self.cwd_browse.clicked.connect(self._on_browse_wd)

        self._load_venvs()

    # ----- Public API -----
    def set_script(self, sid: int, path: str):
        self.current_sid = sid
        self.current_path = path
        extras = get_script_extras(sid)
        # Select venv
        self._load_venvs(select_id=extras.get("venv_id"))
        # Working dir
        wd = extras.get("working_dir")
        if wd:
            self.rb_cwd_custom.setChecked(True)
            self.cwd_edit.setText(wd)
        else:
            self.rb_cwd_script.setChecked(True)
            self.cwd_edit.clear()
        self._update_wd_enabled()
        # Build UI from cached schema if present, else probe
        schema_json = extras.get("args_schema")
        values_json = extras.get("args_values")
        if schema_json:
            try:
                schema = json.loads(schema_json)
            except Exception:
                schema = None
        else:
            schema = None
        if schema:
            self._build_form(schema, json.loads(values_json) if values_json else {})
        else:
            self._probe_help_async()

    def build_args(self) -> list[str]:
        args: list[str] = []
        for key, w in self._option_widgets.items():
            if isinstance(w, QLineEdit):
                v = w.text().strip()
                if v:
                    args.extend([key, v])
            elif hasattr(w, 'isChecked'):
                if w.isChecked():
                    args.append(key)
        return args

    def get_python_executable(self) -> str | None:
        idx = self.env_combo.currentIndex()
        if idx <= 0:
            return None
        vid, _name, python_path = self._venv_items[idx - 1]
        return python_path

    def get_working_dir(self) -> str | None:
        if self.rb_cwd_custom.isChecked():
            p = self.cwd_edit.text().strip()
            return p or None
        return None

    # ----- Internals -----
    def _load_venvs(self, select_id: int | None = None):
        self.env_combo.blockSignals(True)
        self.env_combo.clear()
        self._venv_items = []
        self.env_combo.addItem("System Python")
        select_index = 0
        for row in list_venvs():
            vid, name, _path, python_path, *_rest = row
            self._venv_items.append((vid, name, python_path))
            self.env_combo.addItem(f"{name}")
            if select_id is not None and vid == select_id:
                select_index = len(self._venv_items)
        self.env_combo.setCurrentIndex(select_index)
        self.env_combo.blockSignals(False)

    def _on_env_changed(self, _idx: int):
        if self.current_sid is None:
            return
        idx = self.env_combo.currentIndex()
        venv_id = None if idx == 0 else self._venv_items[idx - 1][0]
        set_script_venv(self.current_sid, venv_id)
        # Optionally re-probe on env change
        self._probe_help_async()

    def _on_workdir_changed(self):
        self._update_wd_enabled()
        if self.current_sid is None:
            return
        wd = self.get_working_dir()
        set_working_dir(self.current_sid, wd)
        # Re-probe since working dir might affect help
        self._probe_help_async()

    def _on_browse_wd(self):
        base = str(Path(self.current_path).parent) if self.current_path else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "作業ディレクトリを選択", base)
        if not folder:
            return
        self.rb_cwd_custom.setChecked(True)
        self.cwd_edit.setText(folder)
        self._on_workdir_changed()

    def _update_wd_enabled(self):
        custom = self.rb_cwd_custom.isChecked()
        self.cwd_edit.setEnabled(custom)
        self.cwd_browse.setEnabled(custom)

    def _on_manage_env(self):
        dlg = VenvManagerDialog(self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            # refresh list
            self._load_venvs()

    def _on_probe(self):
        self._probe_help_async()

    def _clear_form(self):
        # Remove old widgets
        while self.form.count():
            item = self.form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._option_widgets.clear()
        self._history_models.clear()
        self._history_filters.clear()

    def _build_form(self, schema: dict, values: dict):
        self._clear_form()
        options = schema.get("options", []) if isinstance(schema, dict) else []
        for opt in options:
            name = opt.get("name") or opt.get("long") or opt.get("short")
            if not name:
                continue
            takes_value = bool(opt.get("takes_value"))
            label = QLabel(name)
            if takes_value:
                w = QLineEdit()
                val = values.get(name)
                if isinstance(val, str):
                    w.setText(val)
                # Attach history completer
                if self.current_sid is not None:
                    items = list_option_history(self.current_sid, name, limit=20)
                else:
                    items = []
                model = QStringListModel(items, self)
                completer = QCompleter(model, self)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                # 部分一致に設定（ブラウザ風）
                try:
                    completer.setFilterMode(Qt.MatchFlag.MatchContains)
                except Exception:
                    pass
                w.setCompleter(completer)
                # Install Shift+Delete handler on popup
                popup = completer.popup()
                ef = _HistoryEventFilter(self.current_sid or -1, name, model, self)
                popup.installEventFilter(ef)
                self._history_models[name] = model
                self._history_filters[name] = ef
            else:
                from PyQt6.QtWidgets import QCheckBox
                w = QCheckBox()
                val = values.get(name)
                if isinstance(val, bool):
                    w.setChecked(val)
            self._option_widgets[name] = w
            self.form.addRow(label, w)

    def _probe_help_async(self):
        if self.current_path is None or self.current_sid is None:
            return
        python = self.get_python_executable() or sys.executable
        proc = QProcess(self)
        proc.setProgram(python)
        proc.setArguments([self.current_path, "-h"])
        wd = self.get_working_dir() or str(Path(self.current_path).parent)
        proc.setWorkingDirectory(wd)
        buf_out = []
        buf_err = []
        proc.readyReadStandardOutput.connect(lambda: buf_out.append(bytes(proc.readAllStandardOutput()).decode("utf-8", errors="ignore")))
        proc.readyReadStandardError.connect(lambda: buf_err.append(bytes(proc.readAllStandardError()).decode("utf-8", errors="ignore")))

        def on_finish(_code, _status):
            text = "".join(buf_out) + "\n" + "".join(buf_err)
            schema = self._parse_help_to_schema(text)
            update_args_schema(self.current_sid, json.dumps(schema, ensure_ascii=False))
            # Preserve existing values if possible
            extras = get_script_extras(self.current_sid)
            values = json.loads(extras["args_values"]) if extras.get("args_values") else {}
            self._build_form(schema, values)
            proc.deleteLater()

        proc.finished.connect(on_finish)
        proc.start()

    def _parse_help_to_schema(self, help_text: str) -> dict:
        # Very simple heuristic parser for argparse-like help
        options: list[dict] = []
        seen = set()
        for raw in help_text.splitlines():
            line = raw.rstrip()
            if not line.strip().startswith("-"):
                continue
            # split into option part and description by double spaces
            parts = [p for p in line.strip().split("  ") if p]
            if not parts:
                continue
            opt_part = parts[0]
            # split by comma between short and long
            names = [p.strip() for p in opt_part.split(",")]
            long_name = None
            short_name = None
            takes_value = False
            metavar = None
            for nm in names:
                # separate name and metavar by space if any
                tokens = nm.split()
                if not tokens:
                    continue
                name_tok = tokens[0]
                mv = tokens[1] if len(tokens) > 1 else None
                if name_tok.startswith("--"):
                    long_name = name_tok
                elif name_tok.startswith("-"):
                    short_name = name_tok
                if mv and mv.upper() == mv:
                    takes_value = True
                    metavar = mv
                if "=" in name_tok:
                    takes_value = True
            key = long_name or short_name
            if not key or key in seen:
                continue
            seen.add(key)
            options.append({
                "name": key,
                "long": long_name,
                "short": short_name,
                "takes_value": takes_value,
                "metavar": metavar,
            })
        return {"options": options}

    def save_current_values(self):
        if self.current_sid is None:
            return
        values: dict[str, object] = {}
        for key, w in self._option_widgets.items():
            if isinstance(w, QLineEdit):
                val = w.text().strip()
                values[key] = val
                if val:
                    # upsert into history and update completer model (MRU)
                    try:
                        upsert_option_history(self.current_sid, key, val)
                    except Exception:
                        pass
                    model = self._history_models.get(key)
                    if model is not None:
                        lst = model.stringList()
                        if val in lst:
                            lst = [val] + [x for x in lst if x != val]
                        else:
                            lst = [val] + lst
                        if len(lst) > 20:
                            lst = lst[:20]
                        model.setStringList(lst)
            else:
                values[key] = bool(getattr(w, 'isChecked') and w.isChecked())
        update_args_values(self.current_sid, json.dumps(values, ensure_ascii=False))
