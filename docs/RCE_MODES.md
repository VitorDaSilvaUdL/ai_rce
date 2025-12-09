# Modos del sistema RCE — Resumen técnico

## Tabla principal

| **Modo**      | **Bomba B2** | **Válvula EV1** | **Válvula EV2** | **Tapas (TH/TV)** | **Objetivo**                           | **Explicación** |
|---------------|--------------|------------------|------------------|--------------------|-----------------------------------------|------------------|
| **FRÍO**      | **ON**       | **ON**           | OFF              | **ABRIR**          | Enfriar el depósito frío (radiative cooling) | El agua circula hacia el RCE mediante B2 y EV1. Las tapas se abren para radiar calor al cielo nocturno. |
| **CALOR**     | **OFF**      | OFF              | **ON**           | **CERRAR**         | Calentar el depósito caliente (ACS)     | B2 apagada evita el circuito frío. EV2 habilita la circulación hacia ACS. Tapas cerradas para actuar como colector solar. |

---

## Explicación específica: “B2 OFF, EV2 en marcha”

| Elemento | Estado | Significado |
|----------|--------|-------------|
| **B2 OFF** | Bomba del circuito de frío apagada | Indica que *no se produce frío*. |
| **EV2 en marcha (ON)** | Circuito de ACS abierto | Indica que se está calentando agua del depósito caliente. |

### **Conclusión**
**B2 OFF + EV2 ON → Modo CALOR (hot mode).**

---

## Resumen rápido

| Si ves… | Entonces… |
|--------|-----------|
| **B2 ON + EV1 ON + EV2 OFF + TAPAS ABIERTAS** | → **Modo FRÍO** |
| **B2 OFF + EV2 ON + EV1 OFF + TAPAS CERRADAS** | → **Modo CALOR** |


# Diagramas ASCII — Modos del sistema RCE

---

# 1) MODO FRÍO (cold)
# B2 ON · EV1 ON · EV2 OFF · TAPAS ABIERTAS

               ┌────────────────────────────┐
               │        TAPAS ABIERTAS       │
               │   (radiative cooling ON)    │
               └──────────────┬──────────────┘
                              │
                        ┌─────▼─────┐
                        │    RCE     │
                        │  Panel RC  │
                        └─────┬─────┘
                              │
                       (EV1 = ON)
                              │
                        ┌─────▼─────┐
                        │   B2 ON    │
                        │ (bomba frío)│
                        └─────┬─────┘
                              │
                     Flujo hacia depósito
                              │
                        ┌─────▼─────┐
                        │ Depósito   │
                        │   FRÍO     │
                        └────────────┘


Leyenda:
- El agua circula gracias a **B2**.
- **EV1 abierta** permite el paso hacia el RCE.
- **Tapas abiertas** → el panel irradia calor al cielo.
- Se enfría el **depósito frío**.
- **EV2 está cerrada**, por lo que ACS NO participa.

---

# 2) MODO CALOR (hot)
# B2 OFF · EV1 OFF · EV2 ON · TAPAS CERRADAS

                        ┌───────────────────────────┐
                        │        TAPAS CERRADAS      │
                        │ (modo colector solar ON)   │
                        └───────────┬────────────────┘
                                    │
                              ┌─────▼─────┐
                              │    RCE     │
                              │  Colector  │
                              └─────┬─────┘
                                    │
                                (EV1 = OFF)
                                    │
                                (B2 = OFF)
                                    │
                              ┌─────▼─────┐
                              │   EV2 ON   │
                              │ (hacia ACS)│
                              └─────┬─────┘
                                    │
                         Flujo hacia depósito ACS
                                    │
                              ┌─────▼─────┐
                              │ Depósito   │
                              │   CALIENTE │
                              │    (ACS)   │
                              └────────────┘


Leyenda:
- **Tapas cerradas** → el RCE funciona como **colector solar**.
- **B2 está OFF**, por lo que el circuito de frío queda aislado.
- **EV2 ON** dirige el flujo hacia el depósito caliente (ACS).
- **EV1 OFF** mantiene cerrado el circuito del cold tank.

---

# Resumen visual

MODO FRÍO → Panel expuesto al cielo → radiative cooling → se enfría depósito frío.  
MODO CALOR → Panel cubierto → absorción solar → se calienta ACS.

