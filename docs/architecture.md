# Arquitectura del sistema AI-RCE

Este documento describe la **arquitectura del sistema AI-RCE** desarrollado en el marco del proyecto
**BOOST-RCE (TED2021-131446B-I00)**. Se presenta una visión clara y jerárquica del funcionamiento del sistema,
desde la arquitectura global hasta el **detalle del bucle de control del Supervisor de Control (SC)** y
el **flujo interno de la API de Predicción**.

El objetivo es dejar **explícito y comprensible** cómo se orquestan las decisiones de control a partir de
predicciones basadas en *Machine Learning*.

---

## 1. Arquitectura global del sistema

La siguiente figura muestra la arquitectura completa del sistema, integrando:

- El sistema físico **RCE (Radiative Collector and Emitter)**.
- El PLC industrial (control, seguridad y actuadores).
- El software **AI-RCE** (control predictivo).
- La API de predicción y los modelos de *Deep Learning*.
- El almacenamiento de datos y la telemetría.

![Arquitectura completa del sistema RCE](images/diagrama_rce_full.png)

### Descripción general

El sistema RCE combina dos modos de operación complementarios:

- **Captación solar térmica diurna**, para producción de calor (ACS).
- **Enfriamiento radiativo nocturno**, para producción de frío por debajo de la temperatura ambiente.

El PLC Siemens gobierna el sistema físico en tiempo real, mientras que el software **AI-RCE** añade una
capa de **inteligencia predictiva**, permitiendo optimizar la operación en función de la demanda,
la producción esperada y las condiciones ambientales.

---

## 2. Arquitectura del software AI-RCE

La arquitectura del software se basa en una **separación clara de responsabilidades**:

- **Supervisor de Control (SC)**  
  Orquesta el bucle de control y toma decisiones operativas.
- **API de Predicción (FastAPI)**  
  Centraliza la inferencia de modelos y la estimación de producción.
- **Modelos de Machine Learning**  
  Predicen demanda, temperaturas, meteorología y producción.
- **Persistencia de datos**  
  Almacena histórico, telemetría y decisiones.

![Arquitectura del software AI-RCE](images/diagrama_rce_software.png)

---

## 3. Bucle de control del Supervisor de Control (SC)

El **Supervisor de Control (SC)** es el componente central del sistema.  
Su comportamiento se basa en un **bucle periódico** (*while True*) que se ejecuta cada **N minutos (tick)**.

En cada iteración, el SC:
1. Lee el estado actual y el histórico del sistema.
2. Construye la ventana de entrada para predicción.
3. Solicita predicciones a la API.
4. Ejecuta la lógica de decisión mediante `get_decision`.
5. Aplica acciones (si procede).
6. Registra telemetría y decisiones.
7. Espera hasta el siguiente tick.

### Diagrama de secuencia del bucle del SC

El siguiente diagrama de secuencia representa de forma explícita:
- El **bucle temporal del SC**.
- La llamada a la API de predicción.
- La ejecución de `get_decision`.
- La diferenciación entre **acción** y **no acción**.

![Diagrama de secuencia del Supervisor de Control](images/sequence_sc.png)

### Rol de `get_decision`

La función `get_decision` constituye el **núcleo lógico del SC**.  
A partir de las predicciones recibidas y del estado actual del sistema:

- Compara **producción prevista vs demanda prevista**.
- Decide el **modo de operación** (HOT / COLD).
- Determina **ON / OFF** del sistema.
- Gestiona el estado de la **cubierta móvil**.
- Respeta las restricciones de **seguridad** (lluvia, viento, etc.).

El resultado de `get_decision` se traduce posteriormente en comandos hacia el PLC (en operación real).

---

## 4. Funcionamiento interno de la API de Predicción

La **API de Predicción (FastAPI)** actúa como un **servicio de inferencia desacoplado** del control.
Esto permite modificar o mejorar los modelos sin alterar el bucle del SC.

El flujo interno de la API es el siguiente:

1. Recepción de la solicitud `/predict`.
2. Validación del payload.
3. Carga y preparación de datos históricos y actuales.
4. Ejecución de los predictores:
   - DL-Tank (LSTM)
   - DL-Demand (DNN)
   - API-Weather
   - RCE-Production
5. Agregación de resultados.
6. Respuesta JSON al SC.

### Diagrama de flujo de la API

![Diagrama de flujo de la API de Predicción](images/diagrama_flow_api.png)

