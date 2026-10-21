<#
Quita el Panel RCE del arranque del PC (tarea "RCE-Panel", regla del cortafuegos y acceso directo) y para el panel,
la predicción y el control. No borra la carpeta, state\ ni los registros.

  powershell -ExecutionPolicy Bypass -File deploy\uninstall_panel.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host 'ERROR: abre PowerShell (o VS Code) como administrador.' -ForegroundColor Red; exit 1 }
if (Get-ScheduledTask -TaskName 'RCE-Panel' -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName 'RCE-Panel' -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName 'RCE-Panel' -Confirm:$false
  Write-Host '   OK     Tarea «RCE-Panel» eliminada' -ForegroundColor Green
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match [regex]::Escape((Join-Path $Root '.venv')) -or $_.CommandLine -match [regex]::Escape((Join-Path $Root 'deploy\run_'))) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "   OK     Proceso $($_.ProcessId) parado" -ForegroundColor Green }
if (Get-NetFirewallRule -DisplayName 'Panel RCE' -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -DisplayName 'Panel RCE'; Write-Host '   OK     Regla del cortafuegos eliminada' -ForegroundColor Green }
$lnk = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Panel RCE.url'
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '   OK     Acceso directo eliminado' -ForegroundColor Green }
