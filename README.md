# Sistema de Control Térmico: Solar Heating & Radiative Cooling

Este proyecto implementa un sistema de control inteligente para una instalación solar-térmica y de radiative cooling.
El sistema tiene dos partes:

1. Una API de predicción (machine learning) que genera demanda y producción futuras.
2. El software de control que toma decisiones en tiempo real y comunica estados al PLC mediante SNAP7.

## Instalación

### Crear entorno virtual
```
python -m venv venv
```

### Instalar dependencias
```
pip install -r requirements.txt
```

## Ejecución

### 1. Iniciar API de predicción
```
python -m api.main
```

### 2. Iniciar software de control
```
python -m sc.main_vitor
```

## Funcionamiento del algoritmo

El sistema opera en dos modos:
- HOT (7:00–18:59): genera calor aprovechando radiación solar.
- COLD (19:00–06:59): genera frío mediante radiative cooling nocturno.

Cada día calcula demanda y producción prevista, selecciona franjas óptimas y cada 5 segundos decide si encender, mantener o parar el sistema. Se supervisan alarmas de lluvia y viento que pueden forzar cierre inmediato.

## Ciclo principal (diagrama)

```mermaid
flowchart TD

    A[Inicio del ciclo] --> B[Llamar get_decision]

    B --> C{Respuesta de la API}

    C --> D1[yes]
    C --> D2[no]
    C --> D3[parada]

    %% YES
    D1 --> E1[plc.get_system_state]
    E1 --> F1[decide_next_state_from_nn con yes]
    F1 --> G1[final_write_to_plc_nn_mode]
    G1 --> H1[set_heat_mode o set_cold_mode]
    H1 --> I1[sequence_obrir o sequence_tancar]
    I1 --> Z[Esperar 5s y repetir]

    %% NO
    D2 --> E2[plc.get_system_state]
    E2 --> F2[decide_next_state_from_nn con no]
    F2 --> G2[final_write_to_plc_nn_mode manteniendo estado]
    G2 --> Z

    %% PARADA
    D3 --> E3[plc.get_system_state]
    E3 --> F3[decide_next_state_from_nn parada]
    F3 --> G3[final_write_to_plc_nn_mode Parada]
    G3 --> H3[set_parada_mode]
    H3 --> I3[sequence_tancar]
    I3 --> Z
```



```
Preguntar Albert
# ---------------------------------------------------------------------------
# Mapa lógico de variables -> VarName(s) reales + CSV donde buscar
#   source: ruta completa al CSV
#   varnames: lista de posibles nombres en la columna VarName
# ---------------------------------------------------------------------------
VARIABLE_SOURCES = {
    "hot": {
        "varnames": ["TempT6_RCEa", "TempT6_RCEa_v2"],
        "source": lect_dir,   # temperatura caliente
    },
    "cold": {
        "varnames": ["TempT9_RCEa", "TempT9_RCEa_v2"],
        "source": lect_dir,   # temperatura fría
    },
    "v_vent": {
        "varnames": ["VelVent_RCEa", "VelVent_RCEa_v2"],
        "source": lect_dir,   # velocidad viento
    },
    "solar": {
        "varnames": ["IO_SENSOR1_DATA_RCEa"],
        "source": solar_dir,  # radiación solar
    },
    "ir": {
        "varnames": ["E_FIR, neto, [W/m2]_RCEb"],
        "source": ir_dir,     # radiación IR neta
    },
}
```