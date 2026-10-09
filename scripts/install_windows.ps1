$ErrorActionPreference = "Stop"
$Install = Join-Path $env:LOCALAPPDATA "CodexAutoRetry"
New-Item -ItemType Directory -Force -Path $Install | Out-Null
$Exe = Join-Path $PSScriptRoot "CodexAutoRetry.exe"
if (-not (Test-Path $Exe)) { throw "CodexAutoRetry.exe not found next to this installer." }
Copy-Item $Exe (Join-Path $Install "CodexAutoRetry.exe") -Force
$config = Join-Path $Install "config.json"
if (-not (Test-Path $config)) { Copy-Item (Join-Path $PSScriptRoot "config.default.json") $config }
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "Codex Auto Retry.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $Install "CodexAutoRetry.exe"
$Shortcut.WorkingDirectory = $Install
$Shortcut.Description = "Codex Auto Retry"
$Shortcut.Save()
Start-Process (Join-Path $Install "CodexAutoRetry.exe")
Write-Host "Codex Auto Retry installed for $env:USERNAME"
