$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root ".venv-windows"
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) { py -3 -m venv $Venv }
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements-windows.txt")
& (Join-Path $Venv "Scripts\pyinstaller.exe") --noconfirm --clean --onefile --windowed --name CodexAutoRetry --paths (Join-Path $Root "src") --collect-all pystray --collect-all PIL (Join-Path $Root "src\windows_tray.py")
New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist\windows") | Out-Null
Copy-Item (Join-Path $Root "dist\CodexAutoRetry.exe") (Join-Path $Root "dist\windows\CodexAutoRetry.exe") -Force
Copy-Item (Join-Path $Root "config.default.json") (Join-Path $Root "dist\windows\config.default.json") -Force
Copy-Item (Join-Path $Root "scripts\install_windows.ps1") (Join-Path $Root "dist\windows\install_windows.ps1") -Force
Write-Host "Built $Root\dist\windows\CodexAutoRetry.exe"
