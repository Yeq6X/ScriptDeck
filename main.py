import sys
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTableView, QTextEdit, QMessageBox, QAbstractItemView, QMenu,
    QSplitter
)
from PyQt6.QtCore import Qt, QSortFilterProxyModel, QRegularExpression, QSettings
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from repository import import_file, import_directory, fetch_all, save_meta, remove
from runner import ScriptRunner
from widgets import MetaEditDialog, ScriptDetailsPanel, AIAssistantPanel

COLUMNS = ["ID", "名前", "パス", "タグ", "説明", "最終実行", "回数"]

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScriptDeck")
        self.resize(1100, 720)

        # --- Top bar ---
        top = QWidget()
        top_layout = QHBoxLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("検索（名前・タグ）")
        btn_add = QPushButton("追加...")
        btn_import = QPushButton("フォルダ取り込み...")
        # 実行/停止ボタンは右ペインに移動

        top_layout.addWidget(self.search, 1)
        top_layout.addWidget(btn_add)
        top_layout.addWidget(btn_import)
        # 実行/停止ボタンは右ペインに配置するため左側には置かない

        # --- Table ---
        self.model = QStandardItemModel(0, len(COLUMNS), self)
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)  # all columns
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        # --- Log (will be placed under right pane) ---
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("実行ログがここに表示されます")

        # Right details panel (top of right vertical split)
        self.details = ScriptDetailsPanel(self)

        # Compose splitters: left-right (left: search+table, right: details+log)
        left_top = QWidget()
        left_top_layout = QVBoxLayout(left_top)
        left_top_layout.addWidget(top)
        left_top_layout.addWidget(self.table, 1)

        # Left pane: vertical split (top: selection UI, bottom: AI assistant)
        self.ai_panel = AIAssistantPanel(self)
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.addWidget(left_top)
        self.left_split.addWidget(self.ai_panel)
        self.left_split.setStretchFactor(0, 3)
        self.left_split.setStretchFactor(1, 2)

        self.split_main = QSplitter(Qt.Orientation.Horizontal)
        self.split_main.addWidget(self.left_split)
        # Right side is a vertical split: details (top) + log (bottom)
        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(self.details)
        self.right_split.addWidget(self.log)
        self.right_split.setStretchFactor(0, 3)
        self.right_split.setStretchFactor(1, 2)
        self.split_main.addWidget(self.right_split)
        self.split_main.setStretchFactor(0, 3)
        self.split_main.setStretchFactor(1, 2)

        self.setCentralWidget(self.split_main)

        # Runner
        self.runner = ScriptRunner(self)
        self.runner.started.connect(self.on_started)
        self.runner.stdout.connect(lambda s: self.log.insertPlainText(s))
        self.runner.stderr.connect(lambda s: self.log.insertPlainText(s))
        self.runner.finished.connect(self.on_finished)

        # Signals
        self.search.textChanged.connect(self._apply_filter)
        btn_add.clicked.connect(self.add_script)
        btn_import.clicked.connect(self.import_folder)
        # 実行/停止は右ペインのボタンから制御
        self.details.runRequested.connect(self._run_from_details)
        self.details.stopRequested.connect(self.runner.kill)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        # ダブルクリックで右ペインの選択を確定/変更
        self.table.doubleClicked.connect(self._on_double_clicked)

        # Internal flag to ignore selection changes during model rebuilds
        self._suppress_selection_changed = False

        self._restore_ui_state()
        # Initial state: no script selected -> hide right pane
        self._update_right_pane_visibility(False)
        self.load_table()

    # ----- Data -----
    def load_table(self):
        # Suppress selection-changed side effects while rebuilding the model
        self._suppress_selection_changed = True
        self.model.removeRows(0, self.model.rowCount())
        for row in fetch_all():
            self._append_row(row)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        # 右ペインの表示状態はダブルクリックでのみ変更する（未選択になった場合のみ非表示）
        # Re-enable selection change handling
        self._suppress_selection_changed = False

    def _append_row(self, r: dict):
        items = [
            QStandardItem(str(r["id"])),
            QStandardItem(r["name"] or ""),
            QStandardItem(r["path"] or ""),
            QStandardItem(r["tags"] or ""),
            QStandardItem(r["description"] or ""),
            QStandardItem(r["last_run"] or ""),
            QStandardItem(str(r["run_count"] or 0)),
        ]
        for it in items:
            it.setEditable(False)
        self.model.appendRow(items)

    # ----- Actions -----
    def add_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pythonスクリプトを選択", str(Path.home()), "Python (*.py)")
        if not path:
            return
        try:
            sid = import_file(Path(path))
            self.load_table()
            # Select the newly added script
            self._select_by_id(sid)
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def import_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if not dir_path:
            return
        try:
            count = import_directory(Path(dir_path), recurse=True)
            QMessageBox.information(self, "取り込み完了", f"{count}件のスクリプトを取り込みました")
            self.load_table()
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def run_selected(self):
        idx = self._current_index()
        if idx is None:
            QMessageBox.information(self, "情報", "行を選択してください")
            return
        sid = int(self.proxy.index(idx.row(), 0).data())
        path = self.proxy.index(idx.row(), 2).data()
        self.log.clear()
        args = self.details.build_args()
        pyexe = self.details.get_python_executable()
        wd = self.details.get_working_dir()
        self.details.save_current_values()
        self.runner.run(sid, path, args=args, python_executable=pyexe, working_dir=wd)

    def on_started(self, sid: int, cmdline: str):
        self.log.append(f"[RUN] {cmdline}\n")

    def on_finished(self, sid: int, exitCode: int):
        self.log.append(f"\n[EXIT] code={exitCode}\n")
        # Reload table while preserving the selection for the finished script
        self.load_table()
        if sid is not None and sid >= 0:
            self._select_by_id(sid)

    # ----- Settings persistence -----
    def _restore_ui_state(self):
        settings = QSettings("ScriptDeck", "ScriptDeck")
        geom = settings.value("main/geometry")
        if geom is not None:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass
        s_main = settings.value("split/main")
        if s_main is not None:
            try:
                self.split_main.restoreState(s_main)
            except Exception:
                pass
        s_left = settings.value("split/left")
        if s_left is not None:
            try:
                self.left_split.restoreState(s_left)
            except Exception:
                pass
        s_right = settings.value("split/right")
        if s_right is not None:
            try:
                self.right_split.restoreState(s_right)
            except Exception:
                pass
        # AI panel internal split sizes
        try:
            self.ai_panel.restore_settings(settings)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            settings = QSettings("ScriptDeck", "ScriptDeck")
            settings.setValue("main/geometry", self.saveGeometry())
            settings.setValue("split/main", self.split_main.saveState())
            settings.setValue("split/left", self.left_split.saveState())
            settings.setValue("split/right", self.right_split.saveState())
            # AI panel split
            self.ai_panel.save_settings(settings)
            # Save current AI UI state for the selected script
            if getattr(self.ai_panel, 'current_sid', None) is not None:
                try:
                    self.ai_panel._save_ui_state(self.ai_panel.current_sid)
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)

    def _apply_filter(self, text: str):
        # 名前・タグ・説明・パスをまとめてフィルタ（大文字小文字無視／リテラル検索）
        # ユーザ入力は正規表現としてではなく、リテラルとして扱う
        regex = QRegularExpression(QRegularExpression.escape(text))
        regex.setPatternOptions(QRegularExpression.PatternOption.CaseInsensitiveOption)
        self.proxy.setFilterRegularExpression(regex)

    def _current_index(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        return sel[0]

    def _on_selection_changed(self, *_):
        # Ignore selection changes during model rebuilds
        if getattr(self, '_suppress_selection_changed', False):
            return
        idx = self._current_index()
        if idx is None:
            # 未選択になった場合のみ右ペインを未選択状態にする
            self._update_right_pane_visibility(False)
            try:
                if hasattr(self.details, 'clear_selection'):
                    self.details.clear_selection()
            except Exception:
                pass
            return
        # 選択が変わってもダブルクリックされるまで右ペインは変更しない

    def _select_by_id(self, sid: int):
        # Find row in source model
        for row in range(self.model.rowCount()):
            mid = self.model.index(row, 0).data()
            if str(mid) == str(sid):
                src_index = self.model.index(row, 0)
                proxy_index = self.proxy.mapFromSource(src_index)
                self.table.selectRow(proxy_index.row())
                p = self.model.index(row, 2).data()
                # プログラムからの選択は右ペインに反映しない（ダブルクリック時のみ反映）
                break

    def _on_double_clicked(self, index):
        # ダブルクリックされた行を右ペインの選択として反映
        try:
            if not index.isValid():
                return
            row = index.row()
            sid = int(self.proxy.index(row, 0).data())
            path = self.proxy.index(row, 2).data()
            self._update_right_pane_visibility(True)
            self.details.set_script(sid, path)
            self.ai_panel.set_script(sid, path)
        except Exception:
            pass

    def _update_right_pane_visibility(self, visible: bool):
        try:
            self.details.setVisible(visible)
            self.log.setVisible(visible)
        except Exception:
            pass

    # ----- Context Menu -----
    def _context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self)
        act_edit = QAction("メタデータ編集", self)
        act_delete = QAction("削除", self)
        act_run = QAction("実行", self)
        menu.addAction(act_run)
        menu.addAction(act_edit)
        menu.addAction(act_delete)
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        row = self.proxy.mapToSource(idx).row()
        sid = int(self.model.index(row, 0).data())
        name = self.model.index(row, 1).data()
        path = self.model.index(row, 2).data()
        tags = self.model.index(row, 3).data()
        desc = self.model.index(row, 4).data()

        if action == act_run:
            self.log.clear()
            args = self.details.build_args()
            pyexe = self.details.get_python_executable()
            wd = self.details.get_working_dir()
            self.details.save_current_values()
            self.runner.run(sid, path, args=args, python_executable=pyexe, working_dir=wd)
        elif action == act_edit:
            dlg = MetaEditDialog(name, tags, desc, self)
            if dlg.exec() == dlg.DialogCode.Accepted:
                n, t, d = dlg.values()
                save_meta(sid, n or name, t, d)
                self.load_table()
        elif action == act_delete:
            from repository import remove
            remove(sid)
            self.load_table()

    def _run_from_details(self):
        # 右ペインに表示中（ダブルクリックで確定）しているスクリプトを使用
        sid = getattr(self.details, 'current_sid', None)
        path = getattr(self.details, 'current_path', None)
        if sid is None or not path:
            QMessageBox.information(self, "情報", "スクリプトが選択されていません（ダブルクリックで選択）")
            return
        self.log.clear()
        args = self.details.build_args()
        pyexe = self.details.get_python_executable()
        wd = self.details.get_working_dir()
        self.details.save_current_values()
        self.runner.run(int(sid), path, args=args, python_executable=pyexe, working_dir=wd)

def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
