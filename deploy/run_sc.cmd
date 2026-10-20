@echo off
rem ai_rce control system in the mode set in sc\config.prod.json (plc.mode; the lab installer sets "shadow").
rem Started at boot by the scheduled task "RCE-AI SC (sombra)". Waits for the API, restarts sc.main if it stops.
cd /d "%~dp0.."
set SC_ENV=prod
if not exist logs mkdir logs
:loop
powershell -NoProfile -Command "$t=(Get-Date).AddMinutes(10); while((Get-Date) -lt $t){ try { (New-Object Net.Sockets.TcpClient('localhost',8000)).Close(); exit 0 } catch { Start-Sleep 5 } }; exit 1"
echo %date% %time% arrancando sc.main >> logs\sc_task.log
".venv\Scripts\python.exe" -m sc.main >> logs\sc_task.log 2>&1
echo %date% %time% sc.main ha terminado (codigo %errorlevel%); reinicio en 60 s >> logs\sc_task.log
ping -n 61 127.0.0.1 > nul
goto loop
