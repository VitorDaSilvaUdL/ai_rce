<#
Quita ai_rce (modo sombra) del arranque del PC del laboratorio: borra las dos tareas programadas y para sus procesos.
No borra la carpeta, dades.csv ni los registros.

  powershell -ExecutionPolicy Bypass -File deploy\uninstall_shadow_lab.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Venv = Join-Path $Root '.venv'
foreach ($t in 'RCE-AI SC (sombra)', 'RCE-AI API (sombra)') {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    Write-Host "   OK     Tarea «$t» eliminada" -ForegroundColor Green
  }
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match [regex]::Escape($Venv) -or $_.CommandLine -match [regex]::Escape("$Root\deploy\run_")) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "   OK     Proceso $($_.ProcessId) parado" -ForegroundColor Green }
