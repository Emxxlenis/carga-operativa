"""Pronóstico: una línea base honesta sobre el mart.

No es un modelo predictivo. Es un promedio móvil o un estacional semanal
simple, y se rotula así. Lee solo ``mart_resumen_dia`` y ``conciliacion_dia``.

La compuerta vive aquí adentro: ``ejecutar`` comprueba la conciliación del día
por su cuenta y no escribe nada si no cuadra, lo llame quien lo llame.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..db.conexion import ConexionCapa, RutaBase, abrir, transaccion

HORIZONTE_DIAS = 7
VENTANA_DIAS = 28
MIN_OBSERVACIONES = 3
MIN_ESTACIONAL = 14
SEMANAS_ESTACIONAL = 4
DIAS_RETRO = 7
DECIMALES = 3

PROMEDIO_MOVIL = "promedio_movil_7"
ESTACIONAL = "estacional_semanal"
ESTADO_CUADRA = "cuadra"

# (métrica, columna de mart_resumen_dia). Identificadores fijos, no datos.
_METRICAS = (
    ("produccion", "produccion_kg"),
    ("mortalidad", "mortalidad_aves"),
    ("ventas", "ventas_kg"),
)

Serie = dict[dt.date, float]


class ConciliacionNoCuadra(Exception):
    """El día no está conciliado: el pronóstico no corre."""

    def __init__(self, dia: str, estado: str | None) -> None:
        super().__init__(
            f"El pronóstico de {dia} no corre: conciliación en estado '{estado or 'sin calcular'}'."
        )
        self.dia = dia
        self.estado = estado


@dataclass(frozen=True)
class Punto:
    fecha_objetivo: dt.date
    metrica: str
    valor: float
    metodo: str
    n_observaciones: int
    mae_retro: float | None


def _promedio(valores: list[float]) -> float:
    return sum(valores) / len(valores)


def _promedio_movil(serie: Serie, objetivo: dt.date) -> float | None:
    """Promedio de las últimas 7 observaciones anteriores al día objetivo."""
    previas = [serie[fecha] for fecha in sorted(serie) if fecha < objetivo]
    if len(previas) < MIN_OBSERVACIONES:
        return None
    return _promedio(previas[-7:])


def _estacional(serie: Serie, objetivo: dt.date) -> float | None:
    """Promedio del mismo día de la semana en hasta cuatro semanas anteriores."""
    primera = min(serie) if serie else objetivo
    valores: list[float] = []
    fecha = objetivo - dt.timedelta(days=7)
    while fecha >= primera and len(valores) < SEMANAS_ESTACIONAL:
        if fecha in serie:
            valores.append(serie[fecha])
        fecha -= dt.timedelta(days=7)
    return _promedio(valores) if valores else None


_METODOS = {PROMEDIO_MOVIL: _promedio_movil, ESTACIONAL: _estacional}


def _mae_retro(serie: Serie, metodo: str) -> float | None:
    """Error absoluto medio del método, un paso adelante, en los últimos 7 días observados."""
    estimar = _METODOS[metodo]
    errores: list[float] = []
    for fecha in sorted(serie)[-DIAS_RETRO:]:
        anteriores = {f: v for f, v in serie.items() if f < fecha}
        estimado = estimar(anteriores, fecha)
        if estimado is not None:
            errores.append(abs(estimado - serie[fecha]))
    return round(_promedio(errores), DECIMALES) if errores else None


def calcular(serie: Serie, dia_base: dt.date, metrica: str) -> list[Punto]:
    """Línea base de una métrica. Función pura: misma serie, mismo resultado."""
    n = len(serie)
    if n < MIN_OBSERVACIONES:
        return []
    preferido = ESTACIONAL if n >= MIN_ESTACIONAL else PROMEDIO_MOVIL
    errores = {metodo: _mae_retro(serie, metodo) for metodo in _METODOS}
    puntos: list[Punto] = []
    for paso in range(1, HORIZONTE_DIAS + 1):
        objetivo = dia_base + dt.timedelta(days=paso)
        metodo = preferido
        valor = _METODOS[metodo](serie, objetivo)
        if valor is None and metodo == ESTACIONAL:
            # Ese día de la semana no tiene historia: se usa el promedio y se
            # rotula como tal.
            metodo = PROMEDIO_MOVIL
            valor = _promedio_movil(serie, objetivo)
        if valor is None:
            continue
        puntos.append(
            Punto(
                fecha_objetivo=objetivo,
                metrica=metrica,
                valor=round(valor, DECIMALES),
                metodo=metodo,
                n_observaciones=n,
                mae_retro=errores[metodo],
            )
        )
    return puntos


def _estado(conexion: ConexionCapa, dia: str) -> str | None:
    fila = conexion.execute(
        "SELECT estado FROM conciliacion_dia WHERE dia_carga = ?", (dia,)
    ).fetchone()
    return fila[0] if fila else None


def _historia(conexion: ConexionCapa, dia_base: dt.date) -> dict[str, Serie]:
    """Días del mart dentro de la ventana cuya conciliación cuadra."""
    desde = dia_base - dt.timedelta(days=VENTANA_DIAS - 1)
    filas = conexion.execute(
        """
        SELECT m.fecha, m.produccion_kg, m.mortalidad_aves, m.ventas_kg
        FROM mart_resumen_dia AS m
        JOIN conciliacion_dia AS c ON c.dia_carga = m.fecha
        WHERE c.estado = 'cuadra' AND m.fecha BETWEEN ? AND ?
        ORDER BY m.fecha
        """,
        (desde.isoformat(), dia_base.isoformat()),
    ).fetchall()
    series: dict[str, Serie] = {metrica: {} for metrica, _columna in _METRICAS}
    for fecha, *valores in filas:
        for (metrica, _columna), valor in zip(_METRICAS, valores, strict=True):
            if valor is not None:
                series[metrica][dt.date.fromisoformat(fecha)] = float(valor)
    return series


def borrar(ruta_base: RutaBase, dia: str) -> None:
    """Quita el pronóstico de un día base. Es lo único que esta capa puede borrar."""
    conexion = abrir(ruta_base, "pronostico")
    try:
        with transaccion(conexion):
            conexion.execute("DELETE FROM pronostico_linea_base WHERE dia_base = ?", (dia,))
    finally:
        conexion.close()


def ejecutar(ruta_base: RutaBase, dia: str, ahora: str) -> int:
    """Escribe la línea base del día. Devuelve cuántas filas escribió.

    Lanza ``ConciliacionNoCuadra`` y no escribe nada si el día no tiene
    conciliación, o la tiene en 'pendiente' o 'no_cuadra'.
    """
    dia_base = dt.date.fromisoformat(dia)
    conexion = abrir(ruta_base, "pronostico")
    try:
        with transaccion(conexion):
            estado = _estado(conexion, dia)
            if estado != ESTADO_CUADRA:
                raise ConciliacionNoCuadra(dia, estado)
            puntos = [
                punto
                for metrica, serie in _historia(conexion, dia_base).items()
                for punto in calcular(serie, dia_base, metrica)
            ]
            conexion.execute("DELETE FROM pronostico_linea_base WHERE dia_base = ?", (dia,))
            conexion.executemany(
                """
                INSERT INTO pronostico_linea_base
                  (dia_base, fecha_objetivo, metrica, valor, metodo, n_observaciones,
                   mae_retro, generado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        dia,
                        punto.fecha_objetivo.isoformat(),
                        punto.metrica,
                        punto.valor,
                        punto.metodo,
                        punto.n_observaciones,
                        punto.mae_retro,
                        ahora,
                    )
                    for punto in puntos
                ],
            )
        return len(puntos)
    finally:
        conexion.close()
