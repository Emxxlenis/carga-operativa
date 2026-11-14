"""Orquesta un día, en orden fijo. Es el único módulo que conoce todas las capas.

No tiene SQL propio: cada paso es una función de su capa.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import conciliacion
from .config import (
    DIA_DEMO,
    EXTRACTOR_API,
    SEMILLA,
    TIEMPO_LIMITE_API,
    EleccionExtractor,
    Rutas,
    elegir_extractor,
)
from .contrato import aplicar
from .contrato.esquema import VERSION_CONTRATO
from .db import conexion
from .demo import exportar
from .extraccion.api_opcional import ExtractorApi
from .extraccion.determinista import ExtractorDeterminista
from .extraccion.puerto import Extractor
from .landing import recepcion
from .mart import construir as mart
from .modelo import construir as modelo
from .pronostico import linea_base
from .sintetico import generar

_registro = logging.getLogger(__name__)

Reloj = Callable[[], str]

CODIGO_OK = 0
CODIGO_NO_CUADRA = 2


class DiaInvalido(ValueError):
    """El día de carga no es una fecha AAAA-MM-DD."""


@dataclass(frozen=True)
class ResultadoDia:
    dia: str
    estado: str
    controles: tuple[conciliacion.Control, ...]
    recepcion: recepcion.ResumenRecepcion
    archivos: tuple[aplicar.ResumenArchivo, ...]
    filas_pronostico: int

    @property
    def cuadra(self) -> bool:
        return self.estado == conciliacion.CUADRA

    @property
    def controles_fallidos(self) -> tuple[str, ...]:
        return tuple(c.control for c in self.controles if not c.cuadra)

    @property
    def codigo_salida(self) -> int:
        return CODIGO_OK if self.cuadra else CODIGO_NO_CUADRA


def reloj_utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def validar_dia(texto: object) -> str:
    """El día de carga en forma canónica, o ``DiaInvalido``.

    Se valida antes de armar ninguna ruta. ``date.fromisoformat`` acepta además
    otras formas ISO (por ejemplo sin guiones), así que se exige la canónica.
    """
    if not isinstance(texto, str):
        raise DiaInvalido("El día de carga debe ser un texto AAAA-MM-DD.")
    try:
        fecha = dt.date.fromisoformat(texto)
    except ValueError:
        raise DiaInvalido("El día de carga debe ser una fecha AAAA-MM-DD.") from None
    if fecha.isoformat() != texto:
        raise DiaInvalido("El día de carga debe ser una fecha AAAA-MM-DD.")
    return texto


def crear_extractor(eleccion: EleccionExtractor | None = None) -> Extractor:
    """El determinista si no hay clave de API; el opcional si la hay."""
    eleccion = eleccion or elegir_extractor()
    if eleccion.nombre == EXTRACTOR_API:
        return ExtractorApi(
            clave=eleccion.clave,
            url=eleccion.url,
            modelo=eleccion.modelo,
            tiempo_limite=TIEMPO_LIMITE_API,
        )
    return ExtractorDeterminista()


def dia(
    rutas: Rutas,
    dia_carga: str,
    *,
    extractor: Extractor | None = None,
    reloj: Reloj = reloj_utc,
) -> ResultadoDia:
    """Corre un día completo. El pronóstico solo se intenta si el día cuadra."""
    dia_carga = validar_dia(dia_carga)
    extractor = extractor or crear_extractor()
    ahora = reloj()

    conexion.inicializar(rutas.base)
    conciliacion.marcar_pendiente(rutas.base, dia_carga, ahora)
    linea_base.borrar(rutas.base, dia_carga)

    recibido = recepcion.recibir(rutas, dia_carga, ahora)
    archivos = aplicar.aplicar_dia(rutas, dia_carga, extractor, ahora)
    modelo.construir(rutas.base, dia_carga)
    mart.construir(rutas.base, dia_carga)
    resultado = conciliacion.conciliar(rutas.base, dia_carga, ahora)

    filas_pronostico = 0
    if resultado.estado == conciliacion.CUADRA:
        filas_pronostico = linea_base.ejecutar(rutas.base, dia_carga, ahora)
    else:
        _registro.warning(
            "El día %s no cuadra (%s); no se corre el pronóstico.",
            dia_carga,
            ", ".join(resultado.fallidos),
        )
    return ResultadoDia(
        dia=dia_carga,
        estado=resultado.estado,
        controles=resultado.controles,
        recepcion=recibido,
        archivos=tuple(archivos),
        filas_pronostico=filas_pronostico,
    )


def generar_sintetico(rutas: Rutas) -> list[str]:
    """Escribe el buzón de demostración con la semilla fija. Devuelve los días."""
    lotes = [codigo for codigo, *_resto in aplicar.leer_catalogo()]
    return generar.generar(rutas.buzon, lotes)


def exportar_demo(rutas: Rutas, destino: Path, extractor: Extractor) -> None:
    """Genera la página estática del día de la demo."""
    exportar.exportar(
        rutas.base,
        DIA_DEMO,
        destino,
        extractor=extractor.nombre,
        version_contrato=VERSION_CONTRATO,
        semilla=SEMILLA,
    )


def demo(
    rutas: Rutas,
    destino: Path,
    *,
    extractor: Extractor | None = None,
    reloj: Reloj = reloj_utc,
) -> list[ResultadoDia]:
    """Genera los datos sintéticos, corre cada día en orden y exporta la página."""
    extractor = extractor or crear_extractor()
    resultados = [
        dia(rutas, dia_carga, extractor=extractor, reloj=reloj)
        for dia_carga in generar_sintetico(rutas)
    ]
    exportar_demo(rutas, destino, extractor)
    return resultados
