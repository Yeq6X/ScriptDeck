import sys
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, QProcess, QProcessEnvironment
from db import bump_run

class ScriptRunner(QObject):
    started = pyqtSignal(int, str)     # sid, cmdline
    stdout = pyqtSignal(int, str)      # sid, chunk
    stderr = pyqtSignal(int, str)      # sid, chunk
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
        # Ensure Python output is unbuffered for real-time logs
        try:
            env = QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONUNBUFFERED", "1")
            self.proc.setProcessEnvironment(env)
        except Exception:
            pass
        prog = python_executable or sys.executable
        self.proc.setProgram(prog)
        # Force unbuffered mode (-u) so stdout/stderr flush immediately
        py_args = ["-u", script_path, *args]
        self.proc.setArguments(py_args)
        wd = working_dir or str(Path(script_path).parent)
        self.proc.setWorkingDirectory(wd)

        self.proc.readyReadStandardOutput.connect(
            lambda: self.stdout.emit(self.current_sid or -1, bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore"))
        )
        self.proc.readyReadStandardError.connect(
            lambda: self.stderr.emit(self.current_sid or -1, bytes(self.proc.readAllStandardError()).decode("utf-8", errors="ignore"))
        )
        self.proc.finished.connect(self._on_finished)

        cmdline = f"{prog} -u {script_path} " + " ".join(args)
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
