<#
Instala ai_rce en el PC del laboratorio en MODO SOMBRA: lee los CSV de WinCC, predice y decide cada 15 min,
pero no se conecta al PLC ni escribe en él. Cada ciclo queda en dades.csv con el modo y la posición de la tapa
que habría aplicado (execution = shadow); Filebeat lo envía a Kibana.

  powershell -ExecutionPolicy Bypass -File deploy\install_shadow_lab.ps1 -CheckOnly   # comprobaciones, no instala
  powershell -ExecutionPolicy Bypass -File deploy\install_shadow_lab.ps1              # instala y arranca

Instalar en una carpeta nueva (por ejemplo C:\RCE-AI\ai_rce), nunca encima de otra instalación de ai_rce.
Necesita: administrador, Python 3.10-3.12 e internet (paquetes; predicciones de Open-Meteo, AEMET y NASA).
Crea dos tareas programadas que arrancan con el PC: "RCE-AI API (sombra)" y "RCE-AI SC (sombra)".
#>
[CmdletBinding()]
param(
  [string]$WinccDir = '',
  [string]$Python = '',
  [switch]$CheckOnly,
  [switch]$NoTasks,          # prueba: instala y ejecuta un ciclo en primer plano, sin tareas programadas
  [int]$RunSeconds = 120     # con -NoTasks
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path $PSScriptRoot -Parent
$Venv = Join-Path $Root '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$Logs = Join-Path $Root 'logs'
$TaskApi = 'RCE-AI API (sombra)'
$TaskSc = 'RCE-AI SC (sombra)'
$Needed = @('Lecturas0.csv', 'Solarimeter0.csv', 'Pyrgeometer0.csv')

function Say($m)  { Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   OK     $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   AVISO  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host ''; Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

function Run-Logged($exe, [string[]]$argv, $log) {
  # Native programs through Start-Process: Windows PowerShell 5.1 turns their stderr into errors otherwise
  $p = Start-Process -FilePath $exe -ArgumentList $argv -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
         -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
  Get-Content ($log + '.err') -ErrorAction SilentlyContinue | Add-Content $log
  Remove-Item ($log + '.err') -ErrorAction SilentlyContinue
  return $p.ExitCode
}

function Find-Python {
  $cands = @()
  if ($Python) { $cands += ,@($Python) }
  foreach ($v in '3.12', '3.11', '3.10') { $cands += ,@('py', "-$v") }
  $cands += ,@('python')
  foreach ($c in $cands) {
    try {
      $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
      $out = & $exe @pre -c "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])" 2>$null
      if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and @('3.10', '3.11', '3.12') -contains $out[1].Trim()) {
        return @{ Exe = $out[0].Trim(); Version = $out[1].Trim() }
      }
    } catch { }
  }
  return $null
}

# ----------------------------------------------------------------------------------------------------------------
Say '1/6 Comprobaciones'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $CheckOnly -and -not $NoTasks) { Die 'Hace falta PowerShell (o VS Code) abierto como administrador.' }
foreach ($f in 'sc\main.py', 'sc\plc_shadow.py', 'api\main.py', 'deploy\run_api.cmd', 'deploy\run_sc.cmd') {
  if (-not (Test-Path (Join-Path $Root $f))) { Die "No encuentro $f en ${Root}: ¿es la carpeta de ai_rce (rama shadow-mode)?" }
}
Ok "ai_rce en $Root (con modo sombra)"

# another ai_rce already running could be writing to the PLC: never install next to it
$others = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match '(sc|api)\.main' -and $_.CommandLine -notmatch [regex]::Escape($Venv) })
if ($others.Count -gt 0) {
  $list = ($others | ForEach-Object { "PID $($_.ProcessId): $($_.CommandLine)" }) -join "`n   "
  Die "Hay otro ai_rce en marcha en este PC (podría estar escribiendo en el PLC). Páralo antes de seguir:`n   $list"
}
$otherTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)(^|[^a-z])rce([^a-z]|$)' -and $_.TaskName -notin @($TaskApi, $TaskSc) })
if ($otherTasks.Count -gt 0) { Warn ("Tareas programadas con 'rce' en el nombre (revisar que no arranquen otro ai_rce): " + (($otherTasks | ForEach-Object { $_.TaskName }) -join ', ')) }
Ok 'No hay otro ai_rce en marcha'

$listen = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listen -and -not (Get-ScheduledTask -TaskName $TaskApi -ErrorAction SilentlyContinue)) {
  Die "El puerto 8000 (API de ai_rce) ya está en uso por el proceso $($listen[0].OwningProcess)."
}

# WinCC CSV folder
if (-not $WinccDir) {
  $default = 'C:\Users\Usuari\Documents\RCE'
  if (Test-Path (Join-Path $default 'Lecturas0.csv')) { $WinccDir = $default }
  else {
    $hit = Get-ChildItem 'C:\Users' -Filter 'Lecturas0.csv' -File -Recurse -Depth 4 -ErrorAction SilentlyContinue |
           Where-Object { $_.FullName -notmatch [regex]::Escape($Root) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $hit) { Die "No encuentro Lecturas0.csv de WinCC. Indica la carpeta: -WinccDir 'X:\ruta'" }
    $WinccDir = $hit.DirectoryName
  }
}
$WinccDir = $WinccDir.TrimEnd('\')
foreach ($n in $Needed) { if (-not (Test-Path (Join-Path $WinccDir $n))) { Die "Falta $n en $WinccDir" } }
$last = (Get-Item (Join-Path $WinccDir 'Lecturas0.csv')).LastWriteTime
Ok ("Carpeta de WinCC: $WinccDir (Lecturas0.csv: {0:yyyy-MM-dd HH:mm})" -f $last)
if ($last -lt (Get-Date).AddHours(-2)) { Warn 'Lecturas0.csv no se ha actualizado en las últimas 2 h: ¿está WinCC registrando?' }

$py = Find-Python
if (-not $py) { Die "No hay Python 3.10-3.12. Instalar (administrador): winget install -e --id Python.Python.3.12 --scope machine  y volver a ejecutar." }
Ok "Python $($py.Version): $($py.Exe)"

foreach ($h in 'api.open-meteo.com', 'pypi.org') {
  $tcp = New-Object Net.Sockets.TcpClient
  try { $okNet = $tcp.ConnectAsync($h, 443).Wait(5000) -and $tcp.Connected } catch { $okNet = $false } finally { $tcp.Close() }
  if ($okNet) { Ok "Internet: $h" } else { Warn "Sin conexión con ${h}:443 (necesario para instalar paquetes y para las predicciones)" }
}

if ($CheckOnly) {
  Write-Host ''
  Write-Host 'Comprobaciones correctas. Para instalar: powershell -ExecutionPolicy Bypass -File deploy\install_shadow_lab.ps1' -ForegroundColor Green
  exit 0
}

# ----------------------------------------------------------------------------------------------------------------
Say '2/6 Entorno de Python (.venv) y paquetes (TensorFlow: varios minutos la primera vez)'
New-Item -ItemType Directory -Force $Logs | Out-Null
foreach ($t in $TaskSc, $TaskApi) {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Venv) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (-not (Test-Path $VenvPy)) {
  if ((Run-Logged $py.Exe @('-m', 'venv', "`"$Venv`"") (Join-Path $Logs 'venv.log')) -ne 0) { Die "No se pudo crear $Venv (ver logs\venv.log)" }
}
$req = Join-Path $Logs 'requirements-lab.txt'
[IO.File]::WriteAllText($req, [IO.File]::ReadAllText((Join-Path $Root 'api\requirements.txt')), (New-Object Text.UTF8Encoding $false))
Add-Content $req "python-snap7==1.4.1`r`ntqdm`r`npytest"
$pipLog = Join-Path $Logs 'pip_install.log'
if ((Run-Logged $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', "`"$req`"") $pipLog) -ne 0) {
  Die "pip install ha fallado. Últimas líneas de logs\pip_install.log:`n$((Get-Content $pipLog -Tail 15) -join "`n")"
}
Ok 'Paquetes instalados (registro: logs\pip_install.log)'

# ----------------------------------------------------------------------------------------------------------------
Say '3/6 Configuración (sc\config.prod.json)'
$cfgLog = Join-Path $Logs 'configure.log'
if ((Run-Logged $VenvPy @('deploy\configure_lab.py', "`"$WinccDir`"") $cfgLog) -ne 0) { Die "configure_lab.py ha fallado (logs\configure.log)" }
Ok (Get-Content $cfgLog -Tail 1)
$dades = Join-Path $Root 'dades.csv'
if ((Test-Path $dades) -and ((Get-Content $dades -TotalCount 1) -notmatch ',execution,')) {
  # a dades.csv from another installation or the old format must not reach Kibana as if it were from this one
  $old = Join-Path $Logs ('dades_anterior_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.csv')
  Move-Item $dades $old
  Warn "dades.csv anterior (otro formato u otra instalación) movido a $old"
}
$env:SC_ENV = 'prod'
$modeOut = & $VenvPy -c "from sc import config; print(config.PLC_MODE, config.SMTP_ENABLED)"
if ($modeOut -ne 'shadow False') { Die "La configuración no ha quedado en modo sombra sin correo: '$modeOut'" }
Ok 'plc.mode = shadow; correo desactivado'

# ----------------------------------------------------------------------------------------------------------------
Say '4/6 Tests de ai_rce'
$testLog = Join-Path $Logs 'pytest.log'
if ((Run-Logged $VenvPy @('-m', 'pytest', '-q', '-p', 'no:cacheprovider') $testLog) -ne 0) {
  Die "Fallan tests de ai_rce:`n$((Get-Content $testLog -Tail 15) -join "`n")"
}
Ok (Get-Content $testLog | Where-Object { $_ -match 'passed' } | Select-Object -Last 1)

# ----------------------------------------------------------------------------------------------------------------
$dades = Join-Path $Root 'dades.csv'
$fbYml = 'C:\Program Files\Filebeat\filebeat.yml'
if ($NoTasks) {
  Say "5/6 Prueba: API + un ciclo de control en primer plano ($RunSeconds s)"
  $api = Start-Process -FilePath $VenvPy -ArgumentList '-m', 'api.main' -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
           -RedirectStandardOutput (Join-Path $Logs 'api_test.log') -RedirectStandardError (Join-Path $Logs 'api_test.err')
  $sc = $null
  try {
    $deadline = (Get-Date).AddMinutes(3)
    do { Start-Sleep 3; $up = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue } until ($up -or (Get-Date) -gt $deadline)
    if (-not $up) { Die 'La API no ha arrancado en 3 min (logs\api_test.err)' }
    $sc = Start-Process -FilePath $VenvPy -ArgumentList '-m', 'sc.main' -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $Logs 'sc_test.log') -RedirectStandardError (Join-Path $Logs 'sc_test.err')
    Start-Sleep -Seconds $RunSeconds
  } finally {
    foreach ($p in $sc, $api) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
  }
} else {
  Say '5/6 Tareas programadas (arrancan con el PC, como SYSTEM)'
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
  $trigger = New-ScheduledTaskTrigger -AtStartup
  foreach ($t in @(@($TaskApi, 'run_api.cmd'), @($TaskSc, 'run_sc.cmd'))) {
    $action = New-ScheduledTaskAction -Execute (Join-Path $Root "deploy\$($t[1])") -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $t[0] -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
      -Description 'ai_rce en modo sombra: no escribe en el PLC' -Force | Out-Null
    Start-ScheduledTask -TaskName $t[0]
  }
  Ok "Tareas «$TaskApi» y «$TaskSc» creadas y en marcha"

  if (Test-Path $fbYml) {
    $y = [IO.File]::ReadAllText($fbYml)
    $m = [regex]::Match($y, "(?m)^(\s*- ')([^']*dades[^']*\.csv)(')")
    if ($m.Success -and $m.Groups[2].Value -ne $dades) {
      Copy-Item $fbYml ($fbYml + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
      $y = $y.Substring(0, $m.Groups[2].Index) + $dades + $y.Substring($m.Groups[2].Index + $m.Groups[2].Length)
      [IO.File]::WriteAllText($fbYml, $y, (New-Object Text.UTF8Encoding $false))
      Restart-Service filebeat
      Ok "Filebeat ahora lee $dades (antes $($m.Groups[2].Value)); servicio reiniciado"
    } elseif ($m.Success) { Ok "Filebeat ya lee $dades" }
  } else { Warn 'Filebeat no está instalado: las decisiones no llegarán a Kibana (lab\install_lab.ps1 de rce-monitoring)' }
}

# ----------------------------------------------------------------------------------------------------------------
Say '6/6 Comprobación del primer ciclo'
$deadline = (Get-Date).AddMinutes(5); $row = $null
do {
  if (Test-Path $dades) { $row = Get-Content $dades -Tail 1 }
  if ($row -and $row -match ',shadow,') { break }
  Start-Sleep 5
} until ((Get-Date) -gt $deadline)
if (-not ($row -match ',shadow,')) { Die "No hay ningún ciclo en modo sombra en $dades tras 5 min. Ver logs\sc_task.log, logs\api_task.log y sc.log" }
Ok "Primer ciclo: $row"
$plcConn = @(Get-NetTCPConnection -RemotePort 102 -ErrorAction SilentlyContinue | Where-Object {
  (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Path -like "$Venv*" })
if ($plcConn.Count -gt 0) { Die 'ai_rce tiene una conexión abierta al puerto 102 (PLC). No debería en modo sombra: desinstala y avisa.' }
Ok 'Ninguna conexión de ai_rce con el PLC (puerto 102)'

Write-Host ''
Write-Host 'LISTO. ai_rce funciona en MODO SOMBRA: decide cada 15 min y no escribe en el PLC.' -ForegroundColor Green
Write-Host "  Decisiones:   $dades  (columnas execution, plc_state, cover...)"
Write-Host "  Registro:     $Root\sc.log, logs\sc_task.log, logs\api_task.log"
Write-Host '  Kibana:       dashboard «RCE — campaña de frío», fila de arriba (modo y tapa, real vs shadow)'
Write-Host '  Desinstalar:  powershell -ExecutionPolicy Bypass -File deploy\uninstall_shadow_lab.ps1'
