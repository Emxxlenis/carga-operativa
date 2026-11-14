"""Tabla de permisos: qué puede leer, insertar, actualizar y borrar cada capa.

``lee_anterior`` es la frontera del diseño: las tablas de la capa anterior.
``lee_propias`` existe porque SQLite pide permiso de lectura sobre la propia
tabla para resolver ``ON CONFLICT DO UPDATE``, las claves foráneas y el
``WHERE`` de un ``DELETE``. Ninguna capa lee más que eso.
"""

from __future__ import annotations

from dataclasses import dataclass

TABLAS_LANDING = frozenset({"landing_archivo"})
TABLAS_CONTRATO = frozenset({"ref_lote", "contrato_archivo", "contrato_fila_aceptada"})
TABLAS_CUARENTENA = frozenset({"cuarentena"})
TABLAS_MODELO = frozenset({"dim_fecha", "dim_lote", "hecho_dia_lote"})
TABLAS_MART = frozenset({"mart_produccion", "mart_mortalidad", "mart_ventas", "mart_resumen_dia"})
TABLAS_CONCILIACION = frozenset({"conciliacion_dia", "conciliacion_control"})
TABLAS_PRONOSTICO = frozenset({"pronostico_linea_base"})

TABLAS = (
    TABLAS_LANDING
    | TABLAS_CONTRATO
    | TABLAS_CUARENTENA
    | TABLAS_MODELO
    | TABLAS_MART
    | TABLAS_CONCILIACION
    | TABLAS_PRONOSTICO
)

_NADA: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Permiso:
    lee_anterior: frozenset[str]
    lee_propias: frozenset[str] = _NADA
    inserta: frozenset[str] = _NADA
    actualiza: frozenset[str] = _NADA
    borra: frozenset[str] = _NADA
    solo_lectura: bool = False

    @property
    def lee(self) -> frozenset[str]:
        return self.lee_anterior | self.lee_propias


PERMISOS: dict[str, Permiso] = {
    # Landing escribe en cuarentena solo 'entrada_no_regular'; lo impone un
    # trigger del esquema que consulta capa_actual().
    "landing": Permiso(
        lee_anterior=_NADA,
        lee_propias=TABLAS_LANDING,
        inserta=TABLAS_LANDING | TABLAS_CUARENTENA,
    ),
    "contrato": Permiso(
        lee_anterior=TABLAS_LANDING,
        lee_propias=TABLAS_CONTRATO,
        inserta=TABLAS_CONTRATO | TABLAS_CUARENTENA,
    ),
    "modelo": Permiso(
        lee_anterior=frozenset({"contrato_fila_aceptada", "ref_lote"}),
        lee_propias=TABLAS_MODELO,
        inserta=TABLAS_MODELO,
        actualiza=TABLAS_MODELO,
    ),
    "mart": Permiso(
        lee_anterior=TABLAS_MODELO,
        lee_propias=TABLAS_MART,
        inserta=TABLAS_MART,
        actualiza=TABLAS_MART,
    ),
    # El auditor no es una capa: cuenta en todas y escribe solo lo suyo.
    "conciliacion": Permiso(
        lee_anterior=TABLAS - TABLAS_CONCILIACION,
        lee_propias=TABLAS_CONCILIACION,
        inserta=TABLAS_CONCILIACION,
        actualiza=TABLAS_CONCILIACION,
    ),
    "pronostico": Permiso(
        lee_anterior=frozenset({"mart_resumen_dia", "conciliacion_dia"}),
        lee_propias=TABLAS_PRONOSTICO,
        inserta=TABLAS_PRONOSTICO,
        borra=TABLAS_PRONOSTICO,
    ),
    "demo": Permiso(
        lee_anterior=TABLAS_MART | TABLAS_CONCILIACION | TABLAS_PRONOSTICO | TABLAS_CUARENTENA,
        solo_lectura=True,
    ),
}

# Funciones SQL que el código usa. Cualquier otra queda denegada.
FUNCIONES_PERMITIDAS = frozenset({"capa_actual", "coalesce", "count", "sum"})

# Lectura que hace un trigger del esquema en nombre de la capa que inserta:
# (trigger, tabla, columna). Se compara completa porque SQLite informa con el
# mismo campo el nombre de una subconsulta con alias.
LECTURAS_DE_TRIGGER = frozenset(
    {("cuarentena_landing_solo_entrada_no_regular", "cuarentena", "motivo")}
)
