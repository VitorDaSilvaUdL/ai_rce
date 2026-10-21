# AI-RCE — Control predictivo para sistemas híbridos basados en Radiative Collector and Emitter (RCE)

Repositorio de software para **predicción, supervisión y control inteligente** de un sistema híbrido basado en el concepto **RCE (Radiative Collector and Emitter)**, desarrollado en el marco del proyecto estatal **BOOST-RCE (TED2021-131446B-I00)**.

---

## Contexto del proyecto BOOST-RCE

El proyecto **BOOST-RCE** ha permitido optimizar el rendimiento del concepto innovador **RCE (Radiative Collector and Emitter)**, un sistema capaz de generar **calor y frío durante las 24 horas del día** con **emisiones de CO₂ nulas**. El RCE combina **captación solar térmica diurna** y **enfriamiento radiativo nocturno** para producir energía térmica por encima o por debajo de la temperatura ambiente, respectivamente.

Sus principales ventajas competitivas incluyen la **doble generación de energía renovable**, la **modularidad**, la **escalabilidad** y la **capacidad de integración con sistemas convencionales de climatización**, contribuyendo a incrementar la eficiencia energética y a reducir tanto el consumo como las emisiones asociadas.

Con el fin de maximizar la producción de frío nocturno, el sistema incorpora una **cubierta de polietileno de baja densidad (30 μm)** con elevada transmitancia en la **ventana atmosférica infrarroja (7–14 μm)**, donde la atmósfera presenta alta transparencia. Asimismo, se ha desarrollado un **sistema de control lógico inteligente** capaz de optimizar la operación diurna y nocturna. Este sistema compara las **predicciones de producción y demanda** y gestiona la activación de una **cubierta móvil de vidrio** en función de las condiciones ambientales y operativas, con el objetivo de **minimizar el consumo energético** e identificar los **intervalos de máxima eficiencia**.

Los ensayos realizados muestran que el RCE es capaz de alcanzar **temperaturas de agua entre 7,4 °C y 8,1 °C por debajo de la temperatura ambiente**. Paralelamente, los **modelos de predicción basados en técnicas de Deep Learning** han sido validados, proporcionando estimaciones precisas de producción y demanda, lo que permite **optimizar la operación futura del sistema** e identificar escenarios de mayor rendimiento.

Los resultados del proyecto evidencian que el RCE posee un **alto potencial para reducir el consumo de combustibles fósiles y el uso de agua**, así como para mejorar la **seguridad del suministro energético**. Su carácter distribuido disminuye las pérdidas por transporte y contribuye a la estabilidad de precios. En conjunto, el sistema RCE constituye una **tecnología limpia** que mejora el confort térmico, fortalece la sostenibilidad y amplía las posibilidades tecnológicas en los sectores de **climatización y energías renovables**.

---

## Descripción del software

Este repositorio implementa un **sistema de control predictivo basado en Machine Learning** que permite:

- Predecir la **producción térmica (frío/calor)** del sistema RCE.
- Predecir la **demanda energética** del edificio.
- Incorporar **predicciones meteorológicas** relevantes.
- Tomar decisiones automáticas de operación mediante un **Supervisor de Control (SC)**.
- Integrarse con un **PLC Siemens** para la operación del prototipo experimental.

El software ha sido validado sobre un **sistema RCE real**, combinando modelos de Deep Learning con control lógico industrial.

---

## Arquitectura del sistema

```
Sensores / PLC / Base de datos
            ↓
   Supervisor de Control (SC)
            ↓
      API de Predicción
   (FastAPI + Modelos ML)
            ↓
          PLC
            ↓
       Sistema RCE
```


![Arquitectura del software AI-RCE](docs/images/diagrama_rce_software.png)

Ver diagrama detallado en [`docs/architecture.md`](docs/architecture.md)

---

## Bucle de control del Supervisor de Control (SC)

El Supervisor de Control (SC) opera mediante un **bucle periódico de decisión** (tick configurable),
en el que integra estado histórico, predicciones basadas en *Machine Learning* y lógica de control
para decidir la operación óptima del sistema RCE.

![Diagrama de secuencia del bucle del SC](docs/images/sequence_sc.png)

Para una descripción detallada del funcionamiento y de la lógica `get_decision`,
consulte el documento [`docs/architecture.md`](docs/architecture.md).

## Estructura del repositorio

```
ai_rce/
├── api/                # API de predicción (FastAPI)
├── sc/                 # Supervisor de Control
├── docs/               # Documentación técnica y referencias
├── tests/              # Tests unitarios
├── watchdog_sc.py      # Watchdog del sistema
└── README.md
```

---

## Requisitos

- Python ≥ 3.10  
- TensorFlow / Keras  
- FastAPI  
- snap7 (integración con PLC Siemens)  
- Acceso a PLC real o entorno de simulación  

Dependencias:
- `api/requirements.txt`
- `sc/requirements.txt`

---

## Instalación

### API de predicción
```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Supervisor de Control
```bash
cd sc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Ejecución

### Arranque de la API
```bash
python -m api.main
```

Health check:
```
GET http://localhost:8000/health
```

### Arranque del Supervisor de Control
```bash
export SC_ENV=test   # o prod
python -m sc.main
```

### Modo sombra

`plc.mode` en `sc/config.<env>.json` (por defecto `"shadow"`): el control lee WinCC, predice y decide cada 15 min,
pero no se conecta al PLC ni escribe en él; el estado del RCE es virtual (`sc/plc_shadow.py`). Cada ciclo queda en
`dades.csv` con `execution`, estado, modo y tapa (`sc/cycle_log.py`) y en `state/status.json` (`sc/state_io.py`).
`plc.mode = "real"` conecta con el PLC (IP, rack y slot de la configuración). Horarios de calor y parada para el modo
sombra en `plc.shadow.schedule`.

### Panel RCE (página de supervisión y control)

```bash
export SC_ENV=prod
python -m panel.main        # http://localhost:8050 (y desde la red del laboratorio)
```

El panel (`panel/`) arranca y vigila la API y el control (`panel/supervisor.py`), y muestra en el navegador:
semáforos, estado del RCE, última decisión, avisos con qué hacer, lecturas de WinCC, historial, mandos manuales en
seco (`panel/dryrun.py`: la secuencia exacta de escrituras que haría el PLC, simulada), simulador rápido
(`panel/simulator.py`), diario de campaña (`state/diario.csv`) y ajustes. Se entra con nombre y PIN; los mandos exigen
«Tomar el mando» (una sola persona a la vez). Especificación: `rce_review/panel_rce/ESPECIFICACION_panel_rce.md` del
repositorio de trabajo. Instalación en el PC del laboratorio: `deploy/install_shadow_lab.ps1` (ai_rce en sombra) y
después `deploy/install_panel.ps1` (tarea programada `RCE-Panel`, puerto 8050, acceso directo «Panel RCE»).

Fases 2 y 3 del panel: `sc/decision.py` (la decisión en tiempo real como función pura con motivos, que el panel
muestra y usa en el simulador), pestaña Control, simulador completo con el modelo y reproducción de un día
(`panel/simfull.py`), y **modo REAL** desde el panel: activación con doble confirmación (y comprobación de que el PLC
responde y de que ningún otro programa está conectado), mandos reales como trabajos con progreso y aborto
(`panel/plc_ops.py`; exigen el mando tomado y el control en pausa) y estado de salida configurable al volver a SOMBRA.
Probado contra `sc/plc_dummy.py` (PLC Snap7 simulado); en el laboratorio solo con autorización del equipo.

Variables de entorno útiles: `SC_CONFIG` (ruta a un JSON de configuración distinto de `sc/config.<env>.json`),
`PANEL_WORK_DIR` (carpeta para `state/`, registros y `sc.log` fuera del repositorio, para pruebas),
`SC_SMTP_PASSWORD` / `GMAIL_APP_PASSWORD` o `sc/secrets.local.json` (contraseñas; `sc/credentials.py`).

---

## Configuración

La configuración se gestiona mediante ficheros `config.{env}.json` en `sc/`.

⚠️ **Antes de publicar el repositorio**:
- Eliminar credenciales sensibles.
- Usar variables de entorno o ficheros plantilla (`config.template.json`).

---

## Reproducibilidad y limitaciones

- Parte de los datos de demanda pueden ser simulados (TRNSYS).
- La validación completa depende de condiciones meteorológicas reales.
- El sistema está diseñado para **investigación y prototipos pre-industriales**.

---

## Referencias

- Proyecto **BOOST-RCE (TED2021-131446B-I00)** — Ministerio de Ciencia e Innovación, España  
- Vall et al., *Combined Radiative Cooling and Solar Thermal*, **Radiative Collector and Emitter (RCE)**, Energies, 2020  
- Trabajos Fin de Grado y Máster asociados al proyecto (Universitat de Lleida)
