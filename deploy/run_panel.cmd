@echo off
rem Panel RCE: supervisor of the prediction API and the control loop, plus the web page on port 8050.
rem Started at boot by the scheduled task "RCE-Panel". Restarts itself if it stops.
cd /d "%~dp0.."
set SC_ENV=prod
if not exist logs mkdir logs
:loop
echo %date% %time% arrancando panel.main >> logs\panel_task.log
".venv\Scripts\python.exe" -m panel.main >> logs\panel_task.log 2>&1
echo %date% %time% panel.main ha terminado (codigo %errorlevel%); reinicio en 30 s >> logs\panel_task.log
ping -n 31 127.0.0.1 > nul
goto loop
