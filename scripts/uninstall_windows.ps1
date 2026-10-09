$ErrorActionPreference = "Stop"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "Codex Auto Retry.lnk"
if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force }
$Install = Join-Path $env:LOCALAPPDATA "CodexAutoRetry"
if (Test-Path $Install) { Remove-Item $Install -Recurse -Force }
Write-Host "Codex Auto Retry uninstalled"
