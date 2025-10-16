# Usage: Right-click -> Run with PowerShell, or double-click (if policy allows)
# Creates .venv, installs requirements, and launches ScriptDeck.

param(
  [switch]$Reinstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$venvPyw = Join-Path $venv 'Scripts\pythonw.exe'

function Ensure-Venv {
  if (Test-Path $venv -and -not $Reinstall) { return }
  if (Test-Path $venv -and $Reinstall) {
    Write-Host 'Recreating venv...'
    Remove-Item -Recurse -Force $venv
  }
  $launcher = (Get-Command py -ErrorAction SilentlyContinue)
  if ($launcher) {
    & py -m venv $venv
  } else {
    & python -m venv $venv
  }
}

function Ensure-Deps {
  & $venvPy -m pip install -U pip --disable-pip-version-check
  & $venvPy -m pip install -r (Join-Path $root 'requirements.txt')
}

try {
  if (-not (Test-Path $venvPy)) {
    Ensure-Venv
  }
  Ensure-Deps
  $runner = (Test-Path $venvPyw) ? $venvPyw : $venvPy
  & $runner (Join-Path $root 'main.py')
} catch {
  Write-Error $_
  exit 1
}

