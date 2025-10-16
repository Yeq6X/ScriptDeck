@echo off
setlocal
set ROOT=%~dp0
set VENV=%ROOT%\.venv
set VENV_PY=%VENV%\Scripts\python.exe
set VENV_PYW=%VENV%\Scripts\pythonw.exe

if not exist "%VENV_PY%" (
  echo Creating virtual environment...
  where py >nul 2>nul && py -m venv "%VENV%" || python -m venv "%VENV%"
)

"%VENV_PY%" -m pip install -U pip --disable-pip-version-check
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt"

if exist "%VENV_PYW%" (
  set RUNNER=%VENV_PYW%
else (
  set RUNNER=%VENV_PY%
)

"%RUNNER%" "%ROOT%main.py"
endlocal
