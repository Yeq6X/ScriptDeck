import sys
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTreeView, QTextEdit, QMessageBox, QAbstractItemView, QMenu,
    QSplitter, QInputDialog, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QHeaderView, QTabWidget
)
from PyQt6.QtCore import Qt, QSortFilterProxyModel, QRegularExpression, QSettings, QModelIndex, QSize
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction, QTextDocument
from repository import (
    import_file, import_directory, fetch_all, save_meta, remove,
    folders_all, folder_create, folder_rename, folder_delete, folder_move,
    assign_script_to_folder, scripts_in_folder
)
from runner import ScriptRunner
from widgets import MetaEditDialog, ScriptDetailsPanel, AIAssistantPanel, CodePreviewPanel

COLUMNS = ["名前", "タグ", "説明", "最終実行", "回数", "ID", "パス"]

# Roles for tree items
ROLE_NODE_TYPE = Qt.ItemDataRole.UserRole + 1  # 'folder' or 'script'
ROLE_NODE_ID = Qt.ItemDataRole.UserRole + 2    # folder_id or script_id
ROLE_PATH = Qt.ItemDataRole.UserRole + 3       # script path
ROLE_TAGS = Qt.ItemDataRole.UserRole + 4
ROLE_DESC = Qt.ItemDataRole.UserRole + 5

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

        # --- Tree (folders + scripts) ---
        self.model = QStandardItemModel(0, len(COLUMNS), self)
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = RecursiveFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.table = ScriptTreeView(self)
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.setHeaderHidden(False)
        self.table.setExpandsOnDoubleClick(True)
        self.table.setDragDropMode(self.table.DragDropMode.DragDrop)
        self.table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        # Single-line in view with ellipsis; multi-line preview via tooltip
        try:
            self.table.setWordWrap(False)
            self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
            self.table.setUniformRowHeights(True)
            self.table.setItemDelegate(QStyledItemDelegate(self.table))
        except Exception:
            pass

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

        # Left pane: vertical split (top: selection UI, bottom: tabs for AI and Code)
        self.ai_panel = AIAssistantPanel(self)
        self.code_preview = CodePreviewPanel(self)
        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(self.ai_panel, "AI")
        self.left_tabs.addTab(self.code_preview, "コード")
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.addWidget(left_top)
        self.left_split.addWidget(self.left_tabs)
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
        self.table.expanded.connect(self._on_tree_expanded)
        self.table.collapsed.connect(self._on_tree_collapsed)

        # Internal flag to ignore selection changes during model rebuilds
        self._suppress_selection_changed = False

        self._restore_ui_state()
        # Initial state: no script selected -> hide right pane
        self._update_right_pane_visibility(False)
        self.reload_tree()
        self._restore_tree_state()

    # ----- Data -----
    def reload_tree(self):
        # Suppress selection-changed side effects while rebuilding the model
        self._suppress_selection_changed = True
        self.model.removeRows(0, self.model.rowCount())
        # Build mapping for folders
        all_folders = list(folders_all())  # (id, name, parent_id, position)
        by_parent: dict[object, list[tuple]] = {}
        for f in all_folders:
            by_parent.setdefault(f[2], []).append(f)
        for k in list(by_parent.keys()):
            by_parent[k].sort(key=lambda x: (x[3], x[1].lower()))

        def make_row(name: str) -> list[QStandardItem]:
            row = [QStandardItem("") for _ in range(len(COLUMNS))]
            row[0].setText(name)
            try:
                row[0].setToolTip(name or "")
            except Exception:
                pass
            for it in row:
                it.setEditable(False)
            return row

        def add_folder(parent_item: QStandardItem, folder_row: tuple):
            fid, name, parent_id, position = folder_row
            row_items = make_row(name)
            try:
                row_items[0].setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            except Exception:
                pass
            row_items[0].setData('folder', ROLE_NODE_TYPE)
            row_items[0].setData(int(fid), ROLE_NODE_ID)
            parent_item.appendRow(row_items)
            parent_for_children = row_items[0]
            # Add scripts under this folder
            for s in scripts_in_folder(fid):
                srow = self._make_script_row(s)
                parent_for_children.appendRow(srow)
            # Add subfolders
            for ch in by_parent.get(fid, []) or []:
                add_folder(parent_for_children, ch)

        root = self.model.invisibleRootItem()
        # Root scripts
        for s in scripts_in_folder(None):
            srow = self._make_script_row(s)
            root.appendRow(srow)
        # Root folders
        for f in by_parent.get(None, []) or []:
            add_folder(root, f)

        self.table.resizeColumnToContents(0)
        self._suppress_selection_changed = False
        # Restore expanded state after rebuild
        self._restore_tree_state()

    def _make_script_row(self, r: dict | tuple) -> list[QStandardItem]:
        if isinstance(r, dict):
            sid = r.get("id")
            name = r.get("name") or ""
            path = r.get("path") or ""
            tags = r.get("tags") or ""
            desc = r.get("description") or ""
            last_run = r.get("last_run") or ""
            run_count = str(r.get("run_count") or 0)
        else:
            sid, name, path, tags, desc, last_run, run_count, _folder_id = r
            run_count = str(run_count or 0)
        items = [QStandardItem("") for _ in range(len(COLUMNS))]
        items[0].setText(name)
        # Set a generic file icon for scripts
        try:
            items[0].setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        except Exception:
            pass
        try:
            items[0].setToolTip(name or "")
        except Exception:
            pass
        items[1].setText(tags)
        try:
            items[1].setToolTip(tags or "")
        except Exception:
            pass
        items[2].setText(desc)
        # Show multi-line wrapped description in tooltip
        try:
            items[2].setToolTip(self._wrap_tooltip_text(desc or ""))
        except Exception:
            pass
        items[3].setText(last_run)
        items[4].setText(run_count)
        items[5].setText(str(sid))
        items[6].setText(path)
        try:
            items[6].setToolTip(path or "")
        except Exception:
            pass
        for it in items:
            it.setEditable(False)
        items[0].setData('script', ROLE_NODE_TYPE)
        items[0].setData(int(sid), ROLE_NODE_ID)
        items[0].setData(path, ROLE_PATH)
        items[0].setData(tags, ROLE_TAGS)
        items[0].setData(desc, ROLE_DESC)
        return items

    # ----- Actions -----
    def add_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pythonスクリプトを選択", str(Path.home()), "Python (*.py)")
        if not path:
            return
        try:
            folder_id = self._current_folder_id()
            sid = import_file(Path(path), folder_id=folder_id)
            self.reload_tree()
            # Select the newly added script
            self._select_by_id(sid)
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def import_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if not dir_path:
            return
        try:
            folder_id = self._current_folder_id()
            count = import_directory(Path(dir_path), recurse=True, folder_id=folder_id)
            QMessageBox.information(self, "取り込み完了", f"{count}件のスクリプトを取り込みました")
            self.reload_tree()
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def run_selected(self):
        idx = self._current_index()
        if idx is None:
            QMessageBox.information(self, "情報", "行を選択してください")
            return
        src = self.proxy.mapToSource(idx)
        item = self.model.itemFromIndex(src)
        if item is None or item.data(ROLE_NODE_TYPE) != 'script':
            QMessageBox.information(self, "情報", "スクリプトを選択してください")
            return
        sid = int(item.data(ROLE_NODE_ID))
        path = item.data(ROLE_PATH)
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
        # Reload tree while preserving the selection for the finished script
        self.reload_tree()
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
            # Save expanded folders in tree
            self._save_tree_state(settings)
        except Exception:
            pass
        super().closeEvent(event)

    def _apply_filter(self, text: str):
        # 名前・タグ・説明・パスをまとめてフィルタ（大文字小文字無視／リテラル検索）
        # ユーザ入力は正規表現としてではなく、リテラルとして扱う
        regex = QRegularExpression(QRegularExpression.escape(text))
        regex.setPatternOptions(QRegularExpression.PatternOption.CaseInsensitiveOption)
        self.proxy.setFilterRegularExpression(regex)

    def _wrap_tooltip_text(self, text: str, width: int = 60) -> str:
        if not text:
            return ""
        try:
            w = int(width) if width and int(width) > 0 else 60
        except Exception:
            w = 60
        lines = []
        for para in str(text).splitlines() or [""]:
            s = para
            while len(s) > w:
                cut = s.rfind(" ", 0, w)
                if cut <= 0:
                    cut = w
                lines.append(s[:cut])
                s = s[cut:].lstrip()
            lines.append(s)
        return "\n".join(lines)

    def _current_index(self):
        sel = self.table.selectionModel().selectedIndexes()
        if not sel:
            return None
        for i in sel:
            if i.column() == 0:
                return i
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
        self._select_tree_script_by_id(sid)

    def _on_double_clicked(self, index):
        # ダブルクリックされた行を右ペインの選択として反映
        try:
            if not index.isValid():
                return
            src = self.proxy.mapToSource(index)
            item = self.model.itemFromIndex(src)
            if item is None:
                return
            node_type = item.data(ROLE_NODE_TYPE)
            if node_type == 'folder':
                self.table.setExpanded(index, not self.table.isExpanded(index))
                return
            if node_type != 'script':
                return
            sid = item.data(ROLE_NODE_ID)
            path = item.data(ROLE_PATH)
            self._update_right_pane_visibility(True)
            self.details.set_script(sid, path)
            self.ai_panel.set_script(sid, path)
            try:
                self.code_preview.set_script(sid, path)
            except Exception:
                pass
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
        menu = QMenu(self)
        if not idx.isValid():
            act_new = QAction("新規フォルダ...", self)
            menu.addAction(act_new)
            action = menu.exec(self.table.viewport().mapToGlobal(pos))
            if action == act_new:
                self._create_folder_dialog(parent_id=None)
            return
        src = self.proxy.mapToSource(idx)
        item = self.model.itemFromIndex(src)
        if item is None:
            return
        node_type = item.data(ROLE_NODE_TYPE)
        if node_type == 'folder':
            act_new = QAction("新規フォルダ...", self)
            act_rename = QAction("名前変更...", self)
            act_delete = QAction("削除", self)
            menu.addAction(act_new)
            menu.addAction(act_rename)
            menu.addAction(act_delete)
            action = menu.exec(self.table.viewport().mapToGlobal(pos))
            fid = int(item.data(ROLE_NODE_ID))
            if action == act_new:
                self._create_folder_dialog(parent_id=fid)
            elif action == act_rename:
                self._rename_folder_dialog(fid, item.text())
            elif action == act_delete:
                reply = QMessageBox.question(self, "確認", "フォルダを削除しますか？(配下のフォルダは削除、スクリプトはルートに移動)")
                if reply == QMessageBox.StandardButton.Yes:
                    folder_delete(fid)
                    self.reload_tree()
        else:
            act_run = QAction("実行", self)
            act_edit = QAction("メタデータ編集", self)
            act_delete = QAction("削除", self)
            menu.addAction(act_run)
            menu.addAction(act_edit)
            menu.addAction(act_delete)
            action = menu.exec(self.table.viewport().mapToGlobal(pos))
            sid = int(item.data(ROLE_NODE_ID))
            path = item.data(ROLE_PATH)
            tags = item.data(ROLE_TAGS)
            desc = item.data(ROLE_DESC)
            if action == act_run:
                self.log.clear()
                args = self.details.build_args()
                pyexe = self.details.get_python_executable()
                wd = self.details.get_working_dir()
                self.details.save_current_values()
                self.runner.run(int(sid), path, args=args, python_executable=pyexe, working_dir=wd)
            elif action == act_edit:
                name = item.text()
                dlg = MetaEditDialog(name, tags, desc, self)
                if dlg.exec() == dlg.DialogCode.Accepted:
                    n, t, d = dlg.values()
                    save_meta(int(sid), n or name, t, d)
                    self.reload_tree()
            elif action == act_delete:
                remove(int(sid))
                self.reload_tree()

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

    # ----- Tree helpers -----
    def _current_folder_id(self) -> int | None:
        idx = self._current_index()
        if idx is None:
            return None
        src = self.proxy.mapToSource(idx)
        item = self.model.itemFromIndex(src)
        if item is None:
            return None
        if item.data(ROLE_NODE_TYPE) == 'folder':
            return int(item.data(ROLE_NODE_ID))
        parent = item.parent()
        if parent is not None and parent.data(ROLE_NODE_TYPE) == 'folder':
            return int(parent.data(ROLE_NODE_ID))
        return None

    def _select_tree_script_by_id(self, sid: int):
        def dfs(parent_index: QModelIndex) -> bool:
            rows = self.model.rowCount(parent_index)
            for r in range(rows):
                idx0 = self.model.index(r, 0, parent_index)
                item = self.model.itemFromIndex(idx0)
                if item is None:
                    continue
                if item.data(ROLE_NODE_TYPE) == 'script' and int(item.data(ROLE_NODE_ID)) == int(sid):
                    pidx = self.proxy.mapFromSource(idx0)
                    if pidx.isValid():
                        self.table.setCurrentIndex(pidx)
                        # Expand ancestors
                        cur = pidx.parent()
                        while cur.isValid():
                            self.table.expand(cur)
                            cur = cur.parent()
                    return True
                if self.model.hasChildren(idx0):
                    if dfs(idx0):
                        return True
            return False
        dfs(QModelIndex())

    def _on_tree_expanded(self, index: QModelIndex):
        try:
            settings = QSettings("ScriptDeck", "ScriptDeck")
            self._save_tree_state(settings)
        except Exception:
            pass

    def _on_tree_collapsed(self, index: QModelIndex):
        try:
            settings = QSettings("ScriptDeck", "ScriptDeck")
            self._save_tree_state(settings)
        except Exception:
            pass

    def _save_tree_state(self, settings: QSettings):
        expanded_ids: list[int] = []
        def collect(parent_index: QModelIndex):
            rows = self.model.rowCount(parent_index)
            for r in range(rows):
                idx0 = self.model.index(r, 0, parent_index)
                item = self.model.itemFromIndex(idx0)
                if item is None:
                    continue
                if item.data(ROLE_NODE_TYPE) == 'folder':
                    pidx = self.proxy.mapFromSource(idx0)
                    if self.table.isExpanded(pidx):
                        expanded_ids.append(int(item.data(ROLE_NODE_ID)))
                if self.model.hasChildren(idx0):
                    collect(idx0)
        collect(QModelIndex())
        settings.setValue("tree/expanded_folders", expanded_ids)

    def _restore_tree_state(self):
        try:
            settings = QSettings("ScriptDeck", "ScriptDeck")
            vals = settings.value("tree/expanded_folders")
            if vals is None:
                return
            if isinstance(vals, list):
                wanted = {int(v) for v in vals}
            else:
                try:
                    wanted = {int(x) for x in list(vals)}
                except Exception:
                    wanted = set()
            def apply(parent_index: QModelIndex):
                rows = self.model.rowCount(parent_index)
                for r in range(rows):
                    idx0 = self.model.index(r, 0, parent_index)
                    item = self.model.itemFromIndex(idx0)
                    if item is None:
                        continue
                    if item.data(ROLE_NODE_TYPE) == 'folder':
                        fid = int(item.data(ROLE_NODE_ID))
                        if fid in wanted:
                            pidx = self.proxy.mapFromSource(idx0)
                            self.table.expand(pidx)
                    if self.model.hasChildren(idx0):
                        apply(idx0)
            apply(QModelIndex())
        except Exception:
            pass

    def _create_folder_dialog(self, parent_id: int | None):
        name, ok = QInputDialog.getText(self, "新規フォルダ", "フォルダ名:")
        if not ok or not name.strip():
            return
        try:
            folder_create(name.strip(), parent_id)
            self.reload_tree()
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _rename_folder_dialog(self, folder_id: int, current_name: str):
        name, ok = QInputDialog.getText(self, "名前変更", "新しいフォルダ名:", text=current_name)
        if not ok or not name.strip():
            return
        try:
            folder_rename(folder_id, name.strip())
            self.reload_tree()
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))


class WrappingItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        try:
            opt.textElideMode = Qt.TextElideMode.ElideNone
            # enable word wrap if available
            opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        except Exception:
            pass
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

    def sizeHint(self, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        base = super().sizeHint(option, index)
        text = opt.text or ""
        if not text:
            return base
        # Estimate width from view's column width
        width = opt.rect.width()
        view = self.parent()
        try:
            if hasattr(view, 'columnWidth'):
                w2 = view.columnWidth(index.column())
                if isinstance(w2, int) and w2 > 10:
                    width = w2 - 8
        except Exception:
            pass
        doc = QTextDocument()
        try:
            doc.setDefaultFont(opt.font)
        except Exception:
            pass
        doc.setPlainText(text)
        if width <= 0:
            return base
        doc.setTextWidth(width)
        h = int(doc.size().height()) + 6
        return QSize(base.width(), max(base.height(), h))


class RecursiveFilterProxyModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        idx0 = self.sourceModel().index(source_row, 0, source_parent)
        if idx0.isValid():
            text = []
            cols = self.sourceModel().columnCount(idx0)
            for c in range(cols):
                idx = self.sourceModel().index(source_row, c, source_parent)
                if idx.isValid():
                    val = str(self.sourceModel().data(idx) or "")
                    if val:
                        text.append(val)
            combined = " \t".join(text)
            if self.filterRegularExpression().match(combined).hasMatch():
                return True
        child_count = self.sourceModel().rowCount(idx0)
        for r in range(child_count):
            if self.filterAcceptsRow(r, idx0):
                return True
        return False


class ScriptTreeView(QTreeView):
    def dragEnterEvent(self, event):
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            target_index = self.indexAt(event.position().toPoint())
        except Exception:
            target_index = self.indexAt(event.pos())
        window = self.window()
        if not isinstance(window, MainWindow):
            event.ignore()
            return
        folder_id = None
        target_item = None
        if target_index.isValid():
            src = window.proxy.mapToSource(target_index)
            target_item = window.model.itemFromIndex(src)
            if target_item is not None:
                if target_item.data(ROLE_NODE_TYPE) == 'folder':
                    folder_id = int(target_item.data(ROLE_NODE_ID))
                elif target_item.data(ROLE_NODE_TYPE) == 'script':
                    # Use parent folder of the target script
                    parent = target_item.parent()
                    if parent is not None and parent.data(ROLE_NODE_TYPE) == 'folder':
                        folder_id = int(parent.data(ROLE_NODE_ID))
        sel = self.selectionModel().selectedIndexes()
        moved_any = False
        handled = False
        for idx in sel:
            if idx.column() != 0:
                continue
            sidx = window.proxy.mapToSource(idx)
            it = window.model.itemFromIndex(sidx)
            if it is None:
                continue
            ntype = it.data(ROLE_NODE_TYPE)
            if ntype == 'script':
                sid = int(it.data(ROLE_NODE_ID))
                assign_script_to_folder(sid, folder_id)
                moved_any = True
                handled = True
            elif ntype == 'folder':
                fid = int(it.data(ROLE_NODE_ID))
                # Prevent moving a folder into itself or its descendants
                invalid_target = False
                if target_item is not None:
                    if target_item.data(ROLE_NODE_TYPE) == 'folder':
                        tfid = int(target_item.data(ROLE_NODE_ID))
                        if tfid == fid:
                            invalid_target = True
                        else:
                            # Check descendant: DFS from current folder item
                            def is_descendant(curr_item):
                                rows = curr_item.rowCount()
                                for r in range(rows):
                                    child = curr_item.child(r, 0)
                                    if child is None:
                                        continue
                                    if child.data(ROLE_NODE_TYPE) == 'folder':
                                        if int(child.data(ROLE_NODE_ID)) == tfid:
                                            return True
                                        if is_descendant(child):
                                            return True
                                return False
                            if is_descendant(it):
                                invalid_target = True
                if not invalid_target:
                    try:
                        folder_move(fid, folder_id)
                        moved_any = True
                        handled = True
                    except Exception:
                        pass
        if moved_any:
            window.reload_tree()
        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    # ----- Utils -----
    def _wrap_tooltip_text(self, text: str, width: int = 60) -> str:
        if not text:
            return ""
        try:
            w = int(width) if width and int(width) > 0 else 60
        except Exception:
            w = 60
        lines = []
        for para in str(text).splitlines() or [""]:
            s = para
            while len(s) > w:
                # break at last space before limit if possible
                cut = s.rfind(" ", 0, w)
                if cut <= 0:
                    cut = w
                lines.append(s[:cut])
                s = s[cut:].lstrip()
            lines.append(s)
        return "\n".join(lines)

def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
