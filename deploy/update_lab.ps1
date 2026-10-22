<#
Actualiza ai_rce y el Panel RCE en un PC del laboratorio donde el panel YA está instalado. Un solo comando, después de
  git -C <carpeta de ai_rce> pull --ff-only
En PowerShell como administrador, desde la carpeta de ai_rce:
  powershell -ExecutionPolicy Bypass -File deploy\update_lab.ps1 -CheckOnly   # comprueba, no cambia nada
  powershell -ExecutionPolicy Bypass -File deploy\update_lab.ps1              # actualiza

Qué hace: para la tarea «RCE-Panel» (y con ella la predicción y el control), comprueba que no queda ningún python de
esta carpeta, instala los paquetes, reaplica la configuración del laboratorio en modo sombra, pasa los tests, prepara
el certificado propio del panel (HTTPS, state\panel_cert.pem; la clave state\panel_key.pem no se muestra nunca), vuelve
a registrar y arrancar «RCE-Panel», y espera a que el control escriba un ciclo nuevo en modo sombra.
Qué no hace nunca: poner plc.mode = "real", conectar con el PLC (puerto 102), tocar WinCC o sus CSV, tocar el ai_rce
antiguo, tocar state\control.json o state\panel_config.json, ni mostrar contraseñas.
Para una instalación desde cero: deploy\install_shadow_lab.ps1 y después deploy\install_panel.ps1.
#>
[CmdletBinding()]
param(
  [string]$WinccDir = 'C:\Users\Usuari\Documents\RCE',
  [string]$Python = '',            # solo informativo: la actualización usa el .venv que ya existe
  [switch]$CheckOnly,
  [int]$WaitMinutes = 20,          # espera máxima del primer ciclo del control
  [int]$Port = 8050
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'lab_common.ps1')
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$Logs = Join-Path $Root 'logs'
$Task = 'RCE-Panel'
$OldTasks = @('RCE-AI API (sombra)', 'RCE-AI SC (sombra)')
$script:PanelStopped = $false

function Say($m)  { Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   OK     $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   AVISO  $m" -ForegroundColor Yellow }
function Die($m)  {
  Write-Host ''; Write-Host "ERROR: $m" -ForegroundColor Red
  if ($script:PanelStopped) {
    Write-Host ''
    Write-Host 'ATENCIÓN: el Panel RCE está PARADO (y con él la predicción y el control).' -ForegroundColor Red
    Write-Host "Para arrancarlo de nuevo con lo que haya instalado: Start-ScheduledTask -TaskName '$Task'" -ForegroundColor Red
  }
  exit 1
}
function Run-Logged($exe, [string[]]$argv, $log) {
  $p = Start-Process -FilePath $exe -ArgumentList $argv -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
         -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
  Get-Content ($log + '.err') -ErrorAction SilentlyContinue | Add-Content $log
  Remove-Item ($log + '.err') -ErrorAction SilentlyContinue
  return $p.ExitCode
}
function Show-Tree($label) {
  $tree = @(Get-RceProcessTree $Root | Where-Object { $_.Name -match '^python' })
  if ($tree.Count -eq 0) { Ok "$label ningún python de esta carpeta" }
  else { Ok ("$label " + (($tree | ForEach-Object { "PID $($_.ProcessId) $(Get-RceModule $_)" }) -join ' · ')) }
  return $tree
}

# ----------------------------------------------------------------------------------------------------------------
Say '1/7 Comprobaciones'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $CheckOnly) { Die 'Hace falta PowerShell (o VS Code) abierto como administrador.' }
foreach ($f in 'sc\main.py', 'sc\plc_shadow.py', 'panel\main.py', 'panel\agenda.py', 'deploy\run_panel.cmd', 'deploy\configure_lab.py') {
  if (-not (Test-Path (Join-Path $Root $f))) { Die "No encuentro $f en ${Root}: ¿es la carpeta de ai_rce (rama shadow-mode) al día?" }
}
if (-not (Test-Path $VenvPy)) { Die "No existe .venv en ${Root}: es una instalación nueva; usar deploy\install_shadow_lab.ps1 y deploy\install_panel.ps1" }
Ok "ai_rce en $Root"
if (Get-Command git -ErrorAction SilentlyContinue) {
  $head = & git -C $Root log -1 --format='%h %s' 2>$null
  if ($head) { Ok "Versión: $head" }
}
if (-not (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)) {
  Die "No existe la tarea «$Task»: el panel no está instalado; usar deploy\install_panel.ps1"
}
Ok "Tarea «$Task»: $((Get-ScheduledTask -TaskName $Task).State)"
if (-not (Test-Path $WinccDir) -or -not (Get-ChildItem $WinccDir -Filter 'Lecturas*.csv' -ErrorAction SilentlyContinue)) {
  Die "No encuentro los CSV de WinCC (Lecturas*.csv) en $WinccDir. Indica la carpeta con -WinccDir"
}
Ok "WinCC: $WinccDir"
$env:SC_ENV = 'prod'
$modeOut = & $VenvPy -c "from sc import config; print(config.PLC_MODE, config.SMTP_ENABLED)" 2>$null
Ok "Configuración actual: plc.mode, correo = $modeOut"
$tree = Show-Tree 'Procesos de esta instalación:'
$p102 = Get-RcePort102 $Root
if ($p102.Mine.Count -gt 0) { Die "ai_rce tiene conexiones con el puerto 102 (PLC): $($p102.Mine.Count). No debería en modo sombra: no sigas y avisa." }
Ok ("Ninguna conexión de ai_rce con el PLC (puerto 102)" + $(if ($p102.Others.Count) { "; otras conexiones al 102 de otros programas (legítimas): $($p102.OtherNames -join ', ')" } else { '' }))
$crypto = & $VenvPy -c "import importlib.util; print(importlib.util.find_spec('cryptography') is not None)"
if ($crypto -eq 'True') { Ok 'Paquete cryptography (certificado HTTPS del panel) instalado' }
else { Warn 'Falta el paquete cryptography (certificado HTTPS del panel): se instala en el paso 3' }
$public = @(Get-RcePublicProfiles)
if ($public.Count) {
  Warn ("Redes con perfil Público: $($public -join ', '). La regla «Panel RCE» (Dominio y Privado) no se aplica en ellas: " +
        'desde otros PC no se podrá abrir el panel. Este script no cambia el perfil: pídeselo al administrador del PC.')
}
if ($CheckOnly) {
  Write-Host ''
  Write-Host 'Comprobaciones correctas. Para actualizar: powershell -ExecutionPolicy Bypass -File deploy\update_lab.ps1' -ForegroundColor Green
  exit 0
}
New-Item -ItemType Directory -Force $Logs | Out-Null

# ----------------------------------------------------------------------------------------------------------------
Say '2/7 Parar el panel, la predicción y el control'
$script:PanelStopped = $true
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
foreach ($t in $OldTasks) {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
}
$left = @(Stop-RceProcessTree $Root 40)
if ($left.Count) { Die ("No se han podido parar estos procesos de esta instalación: " + (($left | ForEach-Object { "PID $($_.ProcessId) $(Get-RceModule $_)" }) -join ', ')) }
Ok 'Tarea parada; ningún python de esta carpeta en marcha'
foreach ($port in 8000, $Port) {
  $busy = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  if ($busy.Count) {
    $who = ($busy | ForEach-Object { $pr = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "PID $($_.OwningProcess) $($pr.ProcessName)" }) -join ', '
    Die "El puerto $port sigue ocupado por otro programa ($who). No lo paro: no es de esta instalación."
  }
}
Ok 'Puertos 8000 y 8050 libres'

# ----------------------------------------------------------------------------------------------------------------
Say '3/7 Paquetes'
$req = Join-Path $Logs 'requirements-lab.txt'
[IO.File]::WriteAllText($req, [IO.File]::ReadAllText((Join-Path $Root 'api\requirements.txt')), (New-Object Text.UTF8Encoding $false))
Add-Content $req "python-snap7==1.4.1`r`ntqdm`r`npytest`r`nhttpx==0.28.1`r`ncryptography==43.0.0"
$pipLog = Join-Path $Logs 'pip_update.log'
if ((Run-Logged $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', "`"$req`"") $pipLog) -ne 0) {
  Die "pip install ha fallado. Últimas líneas de logs\pip_update.log:`n$((Get-Content $pipLog -Tail 15) -join "`n")"
}
Ok 'Paquetes al día (registro: logs\pip_update.log)'

# ----------------------------------------------------------------------------------------------------------------
Say '4/7 Configuración del laboratorio (modo sombra)'
$cfgLog = Join-Path $Logs 'configure.log'
if ((Run-Logged $VenvPy @('deploy\configure_lab.py', "`"$WinccDir`"") $cfgLog) -ne 0) { Die "configure_lab.py ha fallado (logs\configure.log)" }
Ok (Get-Content $cfgLog -Tail 1)
$modeOut = & $VenvPy -c "from sc import config; print(config.PLC_MODE, config.SMTP_ENABLED)"
if ($modeOut -ne 'shadow False') { Die "La configuración no ha quedado en modo sombra sin correo: '$modeOut'" }
Ok 'plc.mode = shadow; correo desactivado'

# ----------------------------------------------------------------------------------------------------------------
Say '5/7 Tests de ai_rce'
$testLog = Join-Path $Logs 'pytest_update.log'
if ((Run-Logged $VenvPy @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests') $testLog) -ne 0) {
  Die "Fallan tests:`n$((Get-Content $testLog -Tail 20) -join "`n")"
}
Ok (Get-Content $testLog | Where-Object { $_ -match 'passed' } | Select-Object -Last 1)

# ----------------------------------------------------------------------------------------------------------------
Say '6/7 Arrancar el panel'
foreach ($t in $OldTasks) {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    Ok "Tarea antigua «$t» eliminada (la sustituye el panel)"
  }
}
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries `
              -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$action = New-ScheduledTaskAction -Execute (Join-Path $Root 'deploy\run_panel.cmd') -WorkingDirectory $Root
Register-ScheduledTask -TaskName $Task -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal `
  -Settings $settings -Description 'Panel RCE: supervisor de ai_rce (modo sombra) y página web' -Force | Out-Null
if (-not (Get-NetFirewallRule -DisplayName 'Panel RCE' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'Panel RCE' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
  Ok "Regla del cortafuegos «Panel RCE» creada (puerto $Port, redes de dominio y privadas)"
}
$tlsLog = Join-Path $Logs 'tls.log'
$https = (Run-Logged $VenvPy @('-m', 'panel.tls') $tlsLog) -eq 0
if ($https) {
  Get-Content $tlsLog | ForEach-Object { Ok $_ }
  $fp = ((Get-Content $tlsLog | Where-Object { $_ -match 'Huella SHA-256' }) -replace '.*Huella SHA-256:\s*', '')
} else {
  Warn "No se ha podido preparar el certificado HTTPS (logs\tls.log): el panel arrancará en HTTP, sin cifrar."
}
$scheme = if ($https) { 'https' } else { 'http' }
$lnk = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Panel RCE.url'
[IO.File]::WriteAllText($lnk, "[InternetShortcut]`r`nURL=$($scheme)://localhost:$Port/`r`n")
Ok "Acceso directo «Panel RCE» en el escritorio público: $($scheme)://localhost:$Port"
$started = (Get-Date).AddSeconds(-2)
Start-ScheduledTask -TaskName $Task
$deadline = (Get-Date).AddSeconds(120)
$chkLog = Join-Path $Logs 'panel_check.log'
do {
  Start-Sleep 3
  $answer = Run-Logged $VenvPy @('-m', 'panel.tls', '--check', "$Port") $chkLog
} until ($answer -eq 0 -or $answer -eq 2 -or (Get-Date) -gt $deadline)
if ($answer -ne 0 -and $answer -ne 2) { Die "El panel no responde en el puerto $Port tras 120 s. Ver logs\panel_task.log" }
$script:PanelStopped = $false
if ($answer -eq 0) { Ok "Tarea «$Task» registrada y en marcha; /api/me responde por HTTPS con el certificado del panel" }
else {
  $scheme = 'http'
  Warn "El panel responde por HTTP, SIN cifrar (logs\panel_task.log). Tarea «$Task» en marcha."
}

# ----------------------------------------------------------------------------------------------------------------
Say "7/7 Primer ciclo del control (puede tardar unos minutos: se carga el modelo)"
$dades = Join-Path $Root 'dades.csv'
$status = Join-Path $Root 'state\status.json'
$deadline = (Get-Date).AddMinutes($WaitMinutes)
$row = $null
$lastNote = Get-Date
do {
  Start-Sleep 15
  $c = Get-RceLastCycle $dades
  if ($c -and $c.Time -and $c.Time -gt $started -and $c.Execution -eq 'shadow') { $row = $c }
  if (-not $row -and ((Get-Date) - $lastNote).TotalSeconds -ge 60) {
    $mods = @(Get-RceProcessTree $Root | Where-Object { $_.Name -match '^python' } | ForEach-Object { Get-RceModule $_ } | Sort-Object -Unique)
    Write-Host "   …      esperando un ciclo nuevo (procesos: $($mods -join ', '))"
    $lastNote = Get-Date
  }
} until ($row -or (Get-Date) -gt $deadline)
if (-not $row) {
  Write-Host ''
  Write-Host "ERROR: el panel está en marcha, pero el control no ha escrito ningún ciclo nuevo en $WaitMinutes min." -ForegroundColor Red
  Write-Host '  Mirar logs\sc_process.log y logs\api.log, y en el panel: Inicio (semáforos) o Ajustes → «Arrancar el control al encender el PC».' -ForegroundColor Red
  exit 1
}
Ok "Ciclo nuevo en dades.csv: $($row.Time.ToString('yyyy-MM-dd HH:mm:ss')), execution = shadow"
$st = $null
try { $st = Get-Content $status -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
$written = $null
if ($st -and $st.written_at) { try { $written = [datetime]::ParseExact($st.written_at, 'yyyy-MM-dd HH:mm:ss', $null) } catch { } }
if (-not $written -or $written -lt $started) { Die "state\status.json no se ha actualizado desde el arranque (written_at = $($st.written_at))" }
Ok "state\status.json actualizado ($($st.written_at)), ejecución $($st.execution)"
$p102 = Get-RcePort102 $Root
if ($p102.Mine.Count -gt 0) {
  Die "ai_rce tiene una conexión abierta con el puerto 102 (PLC). No debería en modo sombra: para el panel (Stop-ScheduledTask -TaskName '$Task') y avisa."
}
$ips = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $_.IPAddress }) -join ', '
Write-Host ''
Write-Host 'LISTO. ai_rce y Panel RCE actualizados (MODO SOMBRA)' -ForegroundColor Green
Write-Host 'Ninguna conexión de ai_rce con el PLC (puerto 102)' -ForegroundColor Green
if ($p102.Others.Count) { Write-Host "  (Conexiones al 102 de otros programas, legítimas: $($p102.OtherNames -join ', '))" }
Write-Host "  Panel:      $($scheme)://localhost:$Port   ·   desde otros PC: $($scheme)://$($env:COMPUTERNAME):$Port o $($scheme)://<IP>:$Port ($ips)"
if ($scheme -eq 'https') {
  Write-Host "  HTTPS:      certificado propio; el navegador avisará la primera vez. Huella SHA-256 del certificado:"
  Write-Host "              $fp"
  Write-Host "              Para quitar el aviso: abrir $($scheme)://$($env:COMPUTERNAME):$Port/certificado e instalarlo en «Entidades de certificación raíz de confianza»."
}
if ($public.Count) { Write-Host "  Aviso:      redes con perfil Público ($($public -join ', ')): desde otros PC el panel no se abrirá hasta cambiar el perfil." -ForegroundColor Yellow }
Write-Host "  Registros:  $Logs\pip_update.log, configure.log, pytest_update.log, panel_task.log, api.log, sc_process.log"
