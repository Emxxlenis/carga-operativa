"""Validación pura de una fila contra el contrato. Sin E/S.

Hay un solo motivo por fila: el primero que falla, en el orden de
``motivos.MOTIVOS_FILA``. Nada se corrige, se convierte ni se completa.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from ..tipos import Formula
from .esquema import (
    CAMPOS,
    MAXIMOS,
    METRICA_ENTERA,
    METRICAS,
    PATRON_FECHA,
    PATRON_VALOR,
    UNIDAD_POR_METRICA,
)
from .motivos import Motivo

Grano = tuple[str, str, str]


@dataclass(frozen=True)
class Aceptada:
    fecha: str
    lote: str
    metrica: str
    valor: float
    unidad: str

    @property
    def grano(self) -> Grano:
        return (self.fecha, self.lote, self.metrica)


@dataclass(frozen=True)
class Rechazada:
    motivo: Motivo
    campos: dict[str, object] = field(default_factory=dict)


def _vacio(valor: object) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _numero(valor: object) -> float | None:
    """El valor como número, o None si su tipo no se admite."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        try:
            return float(valor)
        except OverflowError:
            return math.inf  # entero enorme: es un número, pero fuera de rango
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else None
    if isinstance(valor, str) and PATRON_VALOR.fullmatch(valor):
        return float(valor)
    return None


def _fecha_iso(valor: object) -> str | None:
    """La fecha en ISO, o None si no es una fecha de calendario AAAA-MM-DD."""
    if isinstance(valor, dt.datetime):
        if valor.time() != dt.time(0, 0):
            return None
        return valor.date().isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    if isinstance(valor, str) and PATRON_FECHA.fullmatch(valor):
        try:
            return dt.date.fromisoformat(valor).isoformat()
        except ValueError:
            return None
    return None


def validar(
    campos: Mapping[str, object],
    *,
    dia_carga: str,
    lotes: Collection[str],
    granos_ocupados: Collection[Grano],
) -> Aceptada | Rechazada:
    # 1. campo_extra
    extras = [clave for clave in campos if clave not in CAMPOS]
    if extras:
        return Rechazada(Motivo.CAMPO_EXTRA, {"campos": extras})

    # 2. columna_faltante
    faltantes = [clave for clave in CAMPOS if _vacio(campos.get(clave))]
    if faltantes:
        return Rechazada(Motivo.COLUMNA_FALTANTE, {"campos": faltantes})

    # 3. formula_no_evaluada
    for clave in CAMPOS:
        if isinstance(campos[clave], Formula):
            return Rechazada(Motivo.FORMULA_NO_EVALUADA, {"campo": clave, "formula": campos[clave]})

    # 4. tipo_invalido
    fecha, lote, metrica, valor, unidad = (campos[clave] for clave in CAMPOS)
    if isinstance(fecha, bool) or not isinstance(fecha, str | dt.date):
        return Rechazada(Motivo.TIPO_INVALIDO, {"campo": "fecha", "valor": fecha})
    for clave, texto in (("lote", lote), ("metrica", metrica), ("unidad", unidad)):
        if not isinstance(texto, str):
            return Rechazada(Motivo.TIPO_INVALIDO, {"campo": clave, "valor": texto})
    numero = _numero(valor)
    if numero is None or (metrica == METRICA_ENTERA and not numero.is_integer()):
        return Rechazada(Motivo.TIPO_INVALIDO, {"campo": "valor", "valor": valor})

    # 5. fecha_invalida
    fecha_iso = _fecha_iso(fecha)
    if fecha_iso is None:
        return Rechazada(Motivo.FECHA_INVALIDA, {"valor": fecha})

    # 6. fecha_fuera_de_dia
    if fecha_iso != dia_carga:
        return Rechazada(Motivo.FECHA_FUERA_DE_DIA, {"valor": fecha_iso, "dia": dia_carga})

    # 7. metrica_desconocida
    if metrica not in METRICAS:
        return Rechazada(Motivo.METRICA_DESCONOCIDA, {"valor": metrica})

    # 8. unidad_distinta
    esperada = UNIDAD_POR_METRICA[metrica]
    recibida = unidad.strip().lower()
    if recibida != esperada:
        return Rechazada(
            Motivo.UNIDAD_DISTINTA,
            {"recibida": unidad, "esperada": esperada, "metrica": metrica},
        )

    # 9. lote_desconocido
    if lote not in lotes:
        return Rechazada(Motivo.LOTE_DESCONOCIDO, {"valor": lote})

    # 10. valor_fuera_de_rango
    maximo = MAXIMOS[metrica]
    if numero < 0 or numero > maximo:
        return Rechazada(
            Motivo.VALOR_FUERA_DE_RANGO,
            {"valor": valor, "metrica": metrica, "maximo": f"{maximo:g} {esperada}"},
        )

    # 11. grano_duplicado
    if (fecha_iso, lote, metrica) in granos_ocupados:
        return Rechazada(
            Motivo.GRANO_DUPLICADO, {"fecha": fecha_iso, "lote": lote, "metrica": metrica}
        )

    return Aceptada(fecha=fecha_iso, lote=lote, metrica=metrica, valor=numero, unidad=esperada)
