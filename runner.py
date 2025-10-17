import sys
import os
import subprocess
import signal
import platform
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
        # Support multiple concurrent processes
        self._next_run_id: int = 1
        self._procs: dict[int, QProcess] = {}
        self._sid_by_run: dict[int, int] = {}
        self._runs_by_sid: dict[int, set[int]] = {}

    def run(self, sid: int, script_path: str, args: list[str] | None = None, python_executable: str | None = None, working_dir: str | None = None):
        args = args or []
        proc = QProcess(self)
        # Ensure Python output is unbuffered for real-time logs
        try:
            env = QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONUNBUFFERED", "1")
            proc.setProcessEnvironment(env)
        except Exception:
            pass
        prog = python_executable or sys.executable
        proc.setProgram(prog)
        # Force unbuffered mode (-u) so stdout/stderr flush immediately
        py_args = ["-u", script_path, *args]
        proc.setArguments(py_args)
        wd = working_dir or str(Path(script_path).parent)
        proc.setWorkingDirectory(wd)

        run_id = self._next_run_id
        self._next_run_id += 1
        self._procs[run_id] = proc
        self._sid_by_run[run_id] = int(sid)
        self._runs_by_sid.setdefault(int(sid), set()).add(run_id)

        # Wire signals, binding sid/run_id/proc at connect-time
        proc.readyReadStandardOutput.connect(
            lambda sid=sid, p=proc: self.stdout.emit(int(sid), bytes(p.readAllStandardOutput()).decode("utf-8", errors="ignore"))
        )
        proc.readyReadStandardError.connect(
            lambda sid=sid, p=proc: self.stderr.emit(int(sid), bytes(p.readAllStandardError()).decode("utf-8", errors="ignore"))
        )
        proc.finished.connect(lambda exitCode, status, run_id=run_id: self._on_finished(run_id, exitCode, status))

        cmdline = f"{prog} -u {script_path} " + " ".join(args)
        if wd:
            cmdline += f" (cwd={wd})"
        self.started.emit(int(sid), cmdline)
        proc.start()

    # ----- Stop/terminate -----
    def kill_sid(self, sid: int):
        for run_id in list(self._runs_by_sid.get(int(sid), set())):
            self.kill_run(run_id)

    def terminate_sid(self, sid: int):
        for run_id in list(self._runs_by_sid.get(int(sid), set())):
            self.terminate_run(run_id)

    def kill_run(self, run_id: int):
        proc = self._procs.get(int(run_id))
        if not proc:
            return
        try:
            pid = int(proc.processId()) if hasattr(proc, 'processId') else None
        except Exception:
            pid = None
        if sys.platform.startswith("win") and pid:
            # Kill entire process tree on Windows to avoid orphaned servers
            try:
                # /T: kill child processes, /F: force
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0)
            except Exception:
                pass
        else:
            # Best-effort: try graceful then force
            try:
                if pid:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.kill()
            except Exception:
                pass

    def terminate_run(self, run_id: int):
        proc = self._procs.get(int(run_id))
        if not proc:
            return
        try:
            pid = int(proc.processId()) if hasattr(proc, 'processId') else None
        except Exception:
            pid = None
        if sys.platform.startswith("win") and pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0)
            except Exception:
                pass
        else:
            try:
                if pid:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.terminate()
            except Exception:
                pass

    def kill_all(self):
        for run_id in list(self._procs.keys()):
            self.kill_run(run_id)

    # ----- Internals -----
    def _on_finished(self, run_id: int, exitCode: int, _status):
        sid = int(self._sid_by_run.get(int(run_id), -1))
        try:
            bump_run(sid, datetime.now(timezone.utc).isoformat())
        except Exception:
            pass
        self.finished.emit(sid, exitCode)
        # Cleanup
        proc = self._procs.pop(int(run_id), None)
        if proc is not None:
            proc.deleteLater()
        if sid in self._runs_by_sid:
            self._runs_by_sid[sid].discard(int(run_id))
            if not self._runs_by_sid[sid]:
                self._runs_by_sid.pop(sid, None)
        self._sid_by_run.pop(int(run_id), None)
