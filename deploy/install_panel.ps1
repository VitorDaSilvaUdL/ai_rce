<#
Instala el Panel RCE en el PC del laboratorio: la página web (http://localhost:8050 y desde la red del laboratorio)
que arranca, vigila y muestra ai_rce. El panel sustituye a las tareas "RCE-AI API (sombra)" y "RCE-AI SC (sombra)":
a partir de ahora es el panel quien arranca la predicción y el control.

  powershell -ExecutionPolicy Bypass -File deploy\install_panel.ps1 -CheckOnly   # comprobaciones, no instala
  powershell -ExecutionPolicy Bypass -File deploy\install_panel.ps1              # instala y arranca

Requisitos: ai_rce ya instalado con deploy\install_shadow_lab.ps1 (.venv y sc\config.prod.json en modo sombra),
PowerShell o VS Code como administrador. Crea la tarea programada "RCE-Panel" (arranca con el PC, usuario SYSTEM),
abre el puerto 8050 en el cortafuegos de Windows, prepara el certificado propio del panel (HTTPS) y deja el acceso
directo "Panel RCE" en el escritorio público.
La primera persona (nombre y PIN) se crea desde el propio PC en la página.
#>
[CmdletBinding()]
param(
  [switch]$CheckOnly,
  [switch]$NoTasks,       # prueba: no crea la tarea ni toca el cortafuegos; arranca el panel en primer plano RunSeconds
  [int]$RunSeconds = 60,
  [int]$Port = 8050
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$Logs = Join-Path $Root 'logs'
$Task = 'RCE-Panel'
$OldTasks = @('RCE-AI API (sombra)', 'RCE-AI SC (sombra)')

function Say($m)  { Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   OK     $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   AVISO  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host ''; Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }
function Run-Logged($exe, [string[]]$argv, $log) {
  $p = Start-Process -FilePath $exe -ArgumentList $argv -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
         -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
  Get-Content ($log + '.err') -ErrorAction SilentlyContinue | Add-Content $log
  Remove-Item ($log + '.err') -ErrorAction SilentlyContinue
  return $p.ExitCode
}

Say '1/5 Comprobaciones'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $CheckOnly -and -not $NoTasks) { Die 'Hace falta PowerShell (o VS Code) abierto como administrador.' }
foreach ($f in 'panel\main.py', 'panel\static\index.html', 'deploy\run_panel.cmd', 'sc\config.prod.json') {
  if (-not (Test-Path (Join-Path $Root $f))) { Die "No encuentro $f en ${Root}: ¿es la carpeta de ai_rce con el panel?" }
}
if (-not (Test-Path $VenvPy)) { Die "No existe .venv: ejecutar antes deploy\install_shadow_lab.ps1" }
Ok "ai_rce con panel en $Root"
$env:SC_ENV = 'prod'
$mode = & $VenvPy -c "from sc import config; print(config.PLC_MODE)"
if ($mode -ne 'shadow') { Die "sc\config.prod.json no está en modo sombra (plc.mode = '$mode')." }
Ok 'Modo sombra'
$imp = & $VenvPy -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)" 2>$null
if ($LASTEXITCODE -ne 0) { Die 'Faltan fastapi/uvicorn en .venv (volver a ejecutar deploy\install_shadow_lab.ps1)' }
Ok "FastAPI/uvicorn $imp"
$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listen -and -not (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)) {
  Die "El puerto $Port ya está en uso por el proceso $($listen[0].OwningProcess)."
}
Ok "Puerto $Port"
. (Join-Path $PSScriptRoot 'lab_common.ps1')
$public = @(Get-RcePublicProfiles)
if ($public.Count) {
  Warn ("Redes con perfil Público: $($public -join ', '). La regla «Panel RCE» (Dominio y Privado) no se aplica en ellas: " +
        'desde otros PC no se podrá abrir el panel. Este script no cambia el perfil: pídeselo al administrador del PC.')
}
$ips = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $_.IPAddress }) -join ', '
Ok "Direcciones de este PC en la red: $ips"
if ($CheckOnly) { Write-Host ''; Write-Host 'Comprobaciones correctas. Para instalar: powershell -ExecutionPolicy Bypass -File deploy\install_panel.ps1' -ForegroundColor Green; exit 0 }

Say '2/5 Tests del panel'
New-Item -ItemType Directory -Force $Logs | Out-Null
$testLog = Join-Path $Logs 'pytest_panel.log'
$p = Start-Process -FilePath $VenvPy -ArgumentList '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests' -WorkingDirectory $Root -NoNewWindow -Wait -PassThru -RedirectStandardOutput $testLog -RedirectStandardError ($testLog + '.err')
Get-Content ($testLog + '.err') -ErrorAction SilentlyContinue | Add-Content $testLog; Remove-Item ($testLog + '.err') -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0) { Die "Fallan tests:`n$((Get-Content $testLog -Tail 15) -join "`n")" }
Ok (Get-Content $testLog | Where-Object { $_ -match 'passed' } | Select-Object -Last 1)

$tlsLog = Join-Path $Logs 'tls.log'
$https = (Run-Logged $VenvPy @('-m', 'panel.tls') $tlsLog) -eq 0
if ($https) {
  Get-Content $tlsLog | ForEach-Object { Ok $_ }
  $fp = ((Get-Content $tlsLog | Where-Object { $_ -match 'Huella SHA-256' }) -replace '.*Huella SHA-256:\s*', '')
} else {
  Warn "No se ha podido preparar el certificado HTTPS (logs\tls.log): el panel arrancará en HTTP, sin cifrar."
}
$scheme = if ($https) { 'https' } else { 'http' }
$chkLog = Join-Path $Logs 'panel_check.log'

if ($NoTasks) {
  Say "3/5 Prueba: panel en primer plano $RunSeconds s"
  $pp = Start-Process -FilePath $VenvPy -ArgumentList '-m', 'panel.main' -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
          -RedirectStandardOutput (Join-Path $Logs 'panel_test.log') -RedirectStandardError (Join-Path $Logs 'panel_test.err')
  try {
    $deadline = (Get-Date).AddSeconds(60)
    do { Start-Sleep 2; $up = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue } until ($up -or (Get-Date) -gt $deadline)
    if (-not $up) { Die "El panel no escucha en $Port (logs\panel_test.err)" }
    $answer = Run-Logged $VenvPy @('-m', 'panel.tls', '--check', "$Port") $chkLog
    if ($answer -eq 1) { Die "El panel no responde en $Port (logs\panel_test.err)" }
    Ok "Panel responde por $(if ($answer -eq 0) { 'HTTPS' } else { 'HTTP, sin cifrar' })"
    Start-Sleep -Seconds $RunSeconds
  } finally { if (-not $pp.HasExited) { Stop-Process -Id $pp.Id -Force } }
  Write-Host ''; Write-Host 'Prueba sin tareas terminada.' -ForegroundColor Green
  exit 0
}

Say '3/5 Tareas programadas'
foreach ($t in $OldTasks) {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    Ok "Tarea «$t» eliminada (ahora la arranca el panel)"
  }
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape((Join-Path $Root '.venv')) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue }
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries `
              -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$action = New-ScheduledTaskAction -Execute (Join-Path $Root 'deploy\run_panel.cmd') -WorkingDirectory $Root
Register-ScheduledTask -TaskName $Task -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal `
  -Settings $settings -Description 'Panel RCE: supervisor de ai_rce (modo sombra) y página web' -Force | Out-Null
Start-ScheduledTask -TaskName $Task
Ok "Tarea «$Task» creada y en marcha"

Say '4/5 Cortafuegos y acceso directo'
if (-not (Get-NetFirewallRule -DisplayName 'Panel RCE' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'Panel RCE' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
  Ok "Puerto $Port abierto en el cortafuegos (redes de dominio y privadas)"
} else { Ok 'Regla del cortafuegos ya existente' }
$desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$lnk = Join-Path $desktop 'Panel RCE.url'
[IO.File]::WriteAllText($lnk, "[InternetShortcut]`r`nURL=$($scheme)://localhost:$Port/`r`n")
Ok "Acceso directo «Panel RCE» en $desktop"

Say '5/5 Comprobación'
$deadline = (Get-Date).AddSeconds(90)
do { Start-Sleep 3; $up = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue } until ($up -or (Get-Date) -gt $deadline)
if (-not $up) { Die "El panel no escucha en $Port tras 90 s. Ver logs\panel_task.log" }
$deadline = (Get-Date).AddSeconds(60)
do { $answer = Run-Logged $VenvPy @('-m', 'panel.tls', '--check', "$Port") $chkLog; if ($answer -eq 1) { Start-Sleep 3 } } until ($answer -ne 1 -or (Get-Date) -gt $deadline)
if ($answer -eq 1) { Die "El panel escucha en $Port pero no responde. Ver logs\panel_task.log" }
if ($answer -eq 2) { $scheme = 'http'; Warn 'El panel responde por HTTP, SIN cifrar (logs\panel_task.log)' }
Ok "Panel en marcha ($($scheme.ToUpper()))"
Write-Host ''
Write-Host 'LISTO. Panel RCE instalado.' -ForegroundColor Green
Write-Host "  En este PC:          $($scheme)://localhost:$Port  (acceso directo «Panel RCE» en el escritorio)"
Write-Host "  Desde otros PC:      $($scheme)://$($env:COMPUTERNAME):$Port o $($scheme)://<IP de este PC>:$Port   ($ips)"
if ($scheme -eq 'https') {
  Write-Host "  HTTPS:               certificado propio; el navegador avisará la primera vez. Huella SHA-256: $fp"
  Write-Host "                       Para quitar el aviso: $($scheme)://$($env:COMPUTERNAME):$Port/certificado, instalarlo en «Entidades de certificación raíz de confianza»."
}
Write-Host '  Primer paso:         abrir el panel en este PC y crear la cuenta rce_admin'
if ($public.Count) { Write-Host "  Aviso:               redes con perfil Público ($($public -join ', ')): desde otros PC el panel no se abrirá hasta cambiar el perfil." -ForegroundColor Yellow }
Write-Host '  Modo REAL:           sin contraseña aún (no se puede activar). Se define en Ajustes desde este PC, con el mando.'
Write-Host "  Registros:           $Logs\panel_task.log, api.log, sc_process.log"
Write-Host '  Desinstalar:         powershell -ExecutionPolicy Bypass -File deploy\uninstall_panel.ps1'
