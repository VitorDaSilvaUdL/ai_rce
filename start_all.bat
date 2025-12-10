@echo off
echo =======================================
echo   Iniciando Sistema RCE (API + SC + Watchdog)
echo =======================================
echo.

REM ---- CONFIGURAR PATH DEL PROYECTO ----
SET PROJECT_PATH=C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\ai_rce\watchdog_sc.py

REM ---- Activar entorno virtual (si usas uno) ----
REM CALL %PROJECT_PATH%\venv\Scripts\activate.bat

echo Iniciando API...
start "API" cmd /k "cd %PROJECT_PATH% && python -m api.main"

echo Iniciando SC (sc.main)...
start "SC" cmd /k "cd %PROJECT_PATH% && python -m sc.main"

echo Iniciando WATCHDOG...
start "WATCHDOG" cmd /k "cd %PROJECT_PATH% && python watchdog_sc.py"

echo Todo iniciado. Puede cerrar esta ventana.
pause