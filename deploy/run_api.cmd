@echo off
rem ai_rce prediction API (FastAPI on localhost:8000). Started at boot by the scheduled task "RCE-AI API (sombra)".
rem Restarts the API if it stops.
cd /d "%~dp0.."
set SC_ENV=prod
if not exist logs mkdir logs
:loop
echo %date% %time% arrancando api.main >> logs\api_task.log
".venv\Scripts\python.exe" -m api.main >> logs\api_task.log 2>&1
echo %date% %time% api.main ha terminado (codigo %errorlevel%); reinicio en 60 s >> logs\api_task.log
ping -n 61 127.0.0.1 > nul
goto loop
