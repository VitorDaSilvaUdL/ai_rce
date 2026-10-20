# Prompt para el agente en el PC del laboratorio: ai_rce en modo sombra

Antes de pegarlo:
1. Copia `ai_rce-sombra-lab.zip` al PC del laboratorio (transferencia de ficheros de TeamViewer) y descomprímelo en
   una carpeta nueva, por ejemplo `C:\RCE-AI\ai_rce`. No lo descomprimas encima de otra instalación de ai_rce.
2. Abre VS Code **como administrador** en esa carpeta.
3. Arranca el agente y pega el bloque de abajo.

```text
Instala ai_rce en este PC del laboratorio en MODO SOMBRA: debe leer los CSV de WinCC, predecir y decidir cada
15 minutos, pero NO conectarse al PLC ni escribir en él (hay pruebas en marcha en todos los componentes). Lee
deploy\install_shadow_lab.ps1, sc\plc_shadow.py y sc\config.prod.json antes de ejecutar nada.

Reglas:
- Nunca cambies plc.mode a "real" ni ejecutes nada que conecte con el PLC (snap7, puerto 102, start_all.bat,
  plc_dummy.py, test_estados3.py).
- No modifiques, muevas ni abras con Excel los ficheros de WinCC. No toques WinCC, el PLC ni otros programas.
- Si ya hay otro ai_rce en marcha o instalado en otra carpeta, no lo pares ni lo borres: para y avísame.
- No muestres contraseñas (sc\config.prod.json tiene una de correo: no la imprimas).

1. Comprobaciones, sin instalar nada:
   powershell -ExecutionPolicy Bypass -File deploy\install_shadow_lab.ps1 -CheckOnly
   Enséñame la carpeta de WinCC detectada, la versión de Python y si hay internet. Si falta Python 3.10-3.12,
   dime el comando de winget que propone el script y espera a que te diga que lo instale.
2. Cuando te confirme, instala:
   powershell -ExecutionPolicy Bypass -File deploy\install_shadow_lab.ps1
   (añade -WinccDir 'ruta' si te doy otra carpeta). La instalación de TensorFlow tarda varios minutos.
3. Debe terminar con «LISTO ... MODO SOMBRA» y «Ninguna conexión de ai_rce con el PLC». Si falla, enséñame el error
   y el final del registro que indique (logs\pip_install.log, logs\sc_task.log, logs\api_task.log, sc.log) y
   propón la corrección antes de aplicarla.
4. Verifica: Get-ScheduledTask "RCE-AI*" (Running), la última línea de dades.csv (execution = shadow) y que
   Filebeat lee ese dades.csv (lo ajusta el instalador).

Al acabar, dime la carpeta de instalación y recuérdame mirar en Kibana la fila de arriba del dashboard (modo y tapa,
real frente a sombra).
```
