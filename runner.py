import sys
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, QProcess
from db import bump_run

class ScriptRunner(QObject):
    started = pyqtSignal(int, str)     # sid, cmdline
    stdout = pyqtSignal(str)
    stderr = pyqtSignal(str)
    finished = pyqtSignal(int, int)    # sid, exitCode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.current_sid: int | None = None

    def run(self, sid: int, script_path: str, args: list[str] | None = None, python_executable: str | None = None, working_dir: str | None = None):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.stderr.emit("実行中のプロセスがあります。停止してから再実行してください。\n")
            return
        args = args or []
        self.current_sid = sid
        self.proc = QProcess(self)
        prog = python_executable or sys.executable
        self.proc.setProgram(prog)
        self.proc.setArguments([script_path, *args])
        wd = working_dir or str(Path(script_path).parent)
        self.proc.setWorkingDirectory(wd)

        self.proc.readyReadStandardOutput.connect(
            lambda: self.stdout.emit(bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore"))
        )
        self.proc.readyReadStandardError.connect(
            lambda: self.stderr.emit(bytes(self.proc.readAllStandardError()).decode("utf-8", errors="ignore"))
        )
        self.proc.finished.connect(self._on_finished)

        cmdline = f"{prog} {script_path} " + " ".join(args)
        if wd:
            cmdline += f" (cwd={wd})"
        self.started.emit(sid, cmdline)
        self.proc.start()

    def terminate(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()

    def kill(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()

    def _on_finished(self, exitCode: int, _status):
        sid = self.current_sid or -1
        bump_run(sid, datetime.now(timezone.utc).isoformat())
        self.finished.emit(sid, exitCode)
        self.proc = None
        self.current_sid = None
