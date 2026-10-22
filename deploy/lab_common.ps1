<#
Funciones comunes de los instaladores del laboratorio (se cargan con: . (Join-Path $PSScriptRoot 'lab_common.ps1')).
Nunca conectan con el PLC ni leen ficheros con contraseñas.
#>

function Get-RceProcessTree([string]$Root) {
  <# Procesos de ESTA instalación: los python.exe / cmd.exe cuya línea de órdenes o ejecutable contiene la carpeta,
     y todos sus descendientes. El python.exe del .venv relanza el intérprete base (...\Python311\python.exe -m api.main),
     cuya línea de órdenes ya no lleva la carpeta: por eso se recorre el árbol. Nunca incluye el PowerShell que ejecuta
     este script ni sus antecesores, ni procesos de otras carpetas (el ai_rce antiguo, WinCC...). #>
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $byId = @{}
  foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }
  $self = New-Object 'System.Collections.Generic.HashSet[int]'
  $cur = [int]$PID
  while ($cur -and $byId.ContainsKey($cur) -and $self.Add($cur)) { $cur = [int]$byId[$cur].ParentProcessId }
  $rx = [regex]::Escape(($Root -replace '/', '\').TrimEnd('\')) + '(\\|"|\s|$)'
  $ids = New-Object 'System.Collections.Generic.HashSet[int]'
  foreach ($p in $all) {
    if ($self.Contains([int]$p.ProcessId)) { continue }
    if ($p.Name -notmatch '^(python|pythonw)\d*(\.\d+)?\.exe$|^cmd\.exe$') { continue }
    $cl = "$($p.CommandLine)" -replace '/', '\'          # rutas escritas con / o con \
    if (($cl -and $cl -match $rx) -or ($p.ExecutablePath -and $p.ExecutablePath -match $rx)) {
      [void]$ids.Add([int]$p.ProcessId)
    }
  }
  do {
    $added = 0
    foreach ($p in $all) {
      $id = [int]$p.ProcessId
      if (-not $ids.Contains($id) -and -not $self.Contains($id) -and $ids.Contains([int]$p.ParentProcessId)) {
        [void]$ids.Add($id); $added++
      }
    }
  } while ($added -gt 0)
  @($all | Where-Object { $ids.Contains([int]$_.ProcessId) })
}

function Get-RceModule($Process) {
  <# Módulo que ejecuta un proceso (panel.main, api.main, sc.main) o el nombre del ejecutable. #>
  if ($Process.CommandLine -match '-m\s+(\S+)') { return $Matches[1] }
  return $Process.Name
}

function Stop-RceProcessTree([string]$Root, [int]$WaitSeconds = 30) {
  <# Para los python.exe de esta instalación (y sus hijos). Devuelve los que quedan vivos. #>
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  do {
    $left = @(Get-RceProcessTree $Root | Where-Object { $_.Name -match '^python' })
    if ($left.Count -eq 0) { return @() }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
  @(Get-RceProcessTree $Root | Where-Object { $_.Name -match '^python' })
}

function Get-RcePort102([string]$Root) {
  <# Conexiones con el puerto 102 (S7, el PLC): las de esta instalación (no debe haber ninguna) y las de otros
     programas (WinCC: S7elonls64.exe, s7oiehsx64.exe...), que son legítimas. #>
  $ids = @(Get-RceProcessTree $Root | ForEach-Object { [int]$_.ProcessId })
  $conns = @(Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $_.RemotePort -eq 102 -or ($_.LocalPort -eq 102) })
  $mine = @($conns | Where-Object { $ids -contains [int]$_.OwningProcess })
  $others = @($conns | Where-Object { $ids -notcontains [int]$_.OwningProcess })
  $names = @($others | ForEach-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName } |
             Where-Object { $_ } | Sort-Object -Unique)
  [pscustomobject]@{ Mine = $mine; Others = $others; OtherNames = $names }
}

function Get-RcePublicProfiles {
  <# Redes con perfil Public: la regla «Panel RCE» (Dominio y Privado) no se aplica en ellas. Solo se informa. #>
  @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { "$($_.NetworkCategory)" -eq 'Public' } |
    ForEach-Object { $_.InterfaceAlias })
}

function Get-RceLastCycle([string]$Dades) {
  <# Última fila de dades.csv: hora (columna timestamp) y ejecución (columna execution). $null si no hay filas. #>
  if (-not (Test-Path $Dades)) { return $null }
  $header = (Get-Content $Dades -TotalCount 1) -split ','
  $last = Get-Content $Dades -Tail 1
  if (-not $last -or $last -eq ($header -join ',')) { return $null }
  $cols = $last -split ','
  $iTs = [array]::IndexOf($header, 'timestamp'); if ($iTs -lt 0) { $iTs = 0 }
  $iEx = [array]::IndexOf($header, 'execution')
  $ts = $null
  try { $ts = [datetime]::ParseExact($cols[$iTs].Trim('"'), 'yyyy-MM-dd HH:mm:ss', $null) } catch { }
  [pscustomobject]@{ Time = $ts; Execution = $(if ($iEx -ge 0 -and $iEx -lt $cols.Count) { $cols[$iEx] } else { '' }) }
}
