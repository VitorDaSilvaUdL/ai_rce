# TODO / TECHNICAL DISCUSSION LIST

Este documento recopila los temas técnicos pendientes de validar o decidir, 
especialmente aquellos que requieren consulta con Albert.

---

## 1. Estados finales de TH/TV tras secuencias Cold/Heat
Referencia: `plc_controller.py` → `_sequence_obrir`, `_sequence_tancar`, `SystemCase`

Descripción:
Después de ejecutar las secuencias automáticas (ColdMode o HeatMode), las
tapas TH y TV quedan en estado MP (Manual-Paro):

- TH = (ActMan=True, ActManMarxa=False)
- TV = (ActMan=True, ActManMarxa=False)

Sin embargo, en `SystemCase` se asumía que debían quedar en MM
(Manual-Marcha). Eso genera combinaciones como:

('A', 'MM', 'MM', 'MP', 'MP', 'MP')

que no están contempladas y hacen que `get_system_state()` retorne `None`.

Decisión provisional (OPCIÓN A):
Ampliar `SystemCase` para aceptar también las combinaciones con MP,
sin modificar de momento las secuencias de movimiento.

Pregunta para Albert:
¿En el PLC real las tapas TH y TV deben quedar en MM o en MP al finalizar
los modos Cold/Heat?

---

## 2. Validar combinaciones adicionales en `SystemCase`
**Referencia:** `plc_controller.py: SystemCase`

### Descripción
El sistema puede generar combinaciones intermedias legítimas que no aparecen 
en SystemCase. Necesitamos saber si deben validarse, ignorarse o agregarse.

### Pregunta para Albert
> ¿SystemCase debe aceptar combinaciones extendidas o solo las estrictamente definidas?

---

## 3. Estados inversos TH/TV (`ActManMarxaInvTH`, `ActManMarxaInvTV`)
**Referencia:** `read_actuators_state()` / secuencias

### Descripción
Los bits inversos existen físicamente y se usan en secuencias, pero no se leen 
en `read_actuators_state`.

### Pregunta para Albert
> ¿Deben incluirse como parte del estado o solo deben usarse internamente durante la secuencia?

---

## 4. Dummy PLC: tiempo real vs. tiempo simulado
**Referencia:** `plc_dummy.py`

### Descripción
Decidir si queremos tiempos realistas (45s motor, 5s pistones) o versiones
aceleradas para desarrollo (3s y 1s).

### Pregunta para Albert
> ¿Simulación rápida está bien para desarrollo o necesitas tiempos más precisos?

---

## 5. Testeo: cobertura de casos intermedios
**Referencia:** carpeta `tests/`

### Descripción
Los tests cubren los modos finales, pero no transiciones parciales.

### Pregunta para Albert
> ¿Debemos testear secuencias paso a paso o solo estados finales?

---

# Fin de TODO.md