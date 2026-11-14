"""Aplica el contrato a los archivos pendientes de un día.

Por archivo: elige el lector, valida cada fila con ``reglas.validar``, escribe
aceptadas y cuarentena, y guarda cuántas filas leyó. Una transacción por
archivo: o queda todo el archivo contado, o no queda nada y sigue pendiente.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from importlib import resources

from .. import cuarentena
from ..config import MAX_BYTES, Rutas
from ..db.conexion import ConexionCapa, abrir, transaccion
from ..extraccion.puerto import ExtraccionNoDisponible, Extractor
from ..landing import recepcion
from ..lectores import correo, excel, xml_seguro
from ..tipos import FilaCruda, RechazoArchivo
from . import reglas
from .esquema import VERSION_CONTRATO

_registro = logging.getLogger(__name__)

ESTADO_PROCESADO = "procesado"
ESTADO_RECHAZADO = "rechazado"
ESTADO_PENDIENTE = "pendiente"
_EXTENSIONES_CON_MACROS = ("xlsm", "xltm")
_CATALOGO = "lotes_demo.csv"


@dataclass(frozen=True)
class ArchivoLanding:
    archivo_id: int
    nombre: str
    extension: str
    formato: str
    bytes: int


@dataclass(frozen=True)
class ResumenArchivo:
    archivo_id: int
    nombre: str
    estado: str
    filas_leidas: int = 0
    aceptadas: int = 0
    rechazadas: int = 0
    motivo_archivo: str | None = None


def leer_catalogo() -> list[tuple[str, str, str, int]]:
    """El catálogo cerrado de lotes de demostración."""
    texto = resources.files(__package__).joinpath(_CATALOGO).read_text(encoding="utf-8")
    return [
        (fila["codigo"], fila["galpon"], fila["fecha_ingreso"], int(fila["aves_iniciales"]))
        for fila in csv.DictReader(texto.splitlines())
    ]


def _cargar_catalogo(conexion: ConexionCapa) -> frozenset[str]:
    with transaccion(conexion):
        conexion.executemany(
            """
            INSERT INTO ref_lote (codigo, galpon, fecha_ingreso, aves_iniciales)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (codigo) DO NOTHING
            """,
            leer_catalogo(),
        )
    return frozenset(fila[0] for fila in conexion.execute("SELECT codigo FROM ref_lote"))


def _filas_de_extraccion(salida: object) -> list[FilaCruda]:
    """Comprueba la forma de lo que devolvió el extractor. No corrige nada."""
    if not isinstance(salida, list):
        raise RechazoArchivo("extraccion_invalida")
    filas: list[FilaCruda] = []
    for numero, registro in enumerate(salida, start=1):
        if not isinstance(registro, dict) or not all(isinstance(c, str) for c in registro):
            raise RechazoArchivo("extraccion_invalida")
        filas.append(FilaCruda(n_fila=numero, campos=dict(registro)))
    return filas


def _leer(
    rutas: Rutas, archivo: ArchivoLanding, extractor: Extractor
) -> tuple[list[FilaCruda], str | None]:
    """Filas crudas del archivo y, si es un correo, el extractor usado."""
    if archivo.extension in _EXTENSIONES_CON_MACROS:
        raise RechazoArchivo("excel_con_macros")
    if archivo.formato == "otro":
        raise RechazoArchivo("extension_no_admitida", extension=archivo.extension)
    if archivo.bytes > MAX_BYTES:
        raise RechazoArchivo("archivo_demasiado_grande")
    datos = recepcion.abrir_verificado(rutas, archivo.archivo_id)

    nombre_extractor = None
    if archivo.formato == "xlsx":
        filas = excel.leer(datos)
    elif archivo.formato == "xml":
        filas = xml_seguro.leer(datos)
    else:
        texto = correo.leer(datos)
        filas = _filas_de_extraccion(extractor.extraer(texto))
        nombre_extractor = extractor.nombre
    if not filas:
        raise RechazoArchivo("archivo_ilegible", causa="sin_filas")
    return filas, nombre_extractor


def _procesar(
    conexion: ConexionCapa,
    rutas: Rutas,
    archivo: ArchivoLanding,
    *,
    dia_carga: str,
    lotes: frozenset[str],
    extractor: Extractor,
    ahora: str,
) -> ResumenArchivo:
    try:
        filas, nombre_extractor = _leer(rutas, archivo, extractor)
    except ExtraccionNoDisponible:
        # Sin recambio de extractor: el archivo queda sin fila de contrato y la
        # conciliación del día lo cuenta como no procesado.
        _registro.warning(
            "Extracción no disponible; el archivo %s queda pendiente.",
            archivo.archivo_id,
            exc_info=True,
        )
        return ResumenArchivo(archivo.archivo_id, archivo.nombre, ESTADO_PENDIENTE)
    except RechazoArchivo as rechazo:
        with transaccion(conexion):
            conexion.execute(
                """
                INSERT INTO contrato_archivo
                  (archivo_id, estado, filas_leidas, version_contrato, extractor, procesado_en)
                VALUES (?, ?, 0, ?, NULL, ?)
                """,
                (archivo.archivo_id, ESTADO_RECHAZADO, VERSION_CONTRATO, ahora),
            )
            cuarentena.registrar(
                conexion,
                dia_carga=dia_carga,
                nombre_archivo=archivo.nombre,
                archivo_id=archivo.archivo_id,
                n_fila=0,
                motivo=rechazo.motivo,
                campos=rechazo.campos,
                fila_cruda=None,
                ahora=ahora,
            )
        return ResumenArchivo(
            archivo.archivo_id, archivo.nombre, ESTADO_RECHAZADO, motivo_archivo=rechazo.motivo
        )

    aceptadas = rechazadas = 0
    with transaccion(conexion):
        ocupados: set[reglas.Grano] = set(
            conexion.execute(
                "SELECT fecha, lote, metrica FROM contrato_fila_aceptada WHERE fecha = ?",
                (dia_carga,),
            )
        )
        conexion.execute(
            """
            INSERT INTO contrato_archivo
              (archivo_id, estado, filas_leidas, version_contrato, extractor, procesado_en)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                archivo.archivo_id,
                ESTADO_PROCESADO,
                len(filas),
                VERSION_CONTRATO,
                nombre_extractor,
                ahora,
            ),
        )
        for fila in filas:
            resultado = reglas.validar(
                fila.campos, dia_carga=dia_carga, lotes=lotes, granos_ocupados=ocupados
            )
            if isinstance(resultado, reglas.Aceptada):
                conexion.execute(
                    """
                    INSERT INTO contrato_fila_aceptada
                      (archivo_id, n_fila, fecha, lote, metrica, valor, unidad)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        archivo.archivo_id,
                        fila.n_fila,
                        resultado.fecha,
                        resultado.lote,
                        resultado.metrica,
                        resultado.valor,
                        resultado.unidad,
                    ),
                )
                ocupados.add(resultado.grano)
                aceptadas += 1
            else:
                cuarentena.registrar(
                    conexion,
                    dia_carga=dia_carga,
                    nombre_archivo=archivo.nombre,
                    archivo_id=archivo.archivo_id,
                    n_fila=fila.n_fila,
                    motivo=resultado.motivo,
                    campos=resultado.campos,
                    fila_cruda=fila.campos,
                    ahora=ahora,
                )
                rechazadas += 1
    return ResumenArchivo(
        archivo.archivo_id,
        archivo.nombre,
        ESTADO_PROCESADO,
        filas_leidas=len(filas),
        aceptadas=aceptadas,
        rechazadas=rechazadas,
    )


def aplicar_dia(
    rutas: Rutas, dia_carga: str, extractor: Extractor, ahora: str
) -> list[ResumenArchivo]:
    """Procesa cada archivo del día que todavía no tiene fila en ``contrato_archivo``."""
    conexion = abrir(rutas.base, "contrato")
    try:
        lotes = _cargar_catalogo(conexion)
        pendientes = [
            ArchivoLanding(
                archivo_id=archivo_id,
                nombre=nombre,
                extension=relativa.rsplit(".", 1)[-1],
                formato=formato,
                bytes=total,
            )
            for archivo_id, nombre, relativa, formato, total in conexion.execute(
                """
                SELECT l.archivo_id, l.nombre_original, l.ruta_relativa, l.formato, l.bytes
                FROM landing_archivo AS l
                WHERE l.dia_carga = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM contrato_archivo AS c WHERE c.archivo_id = l.archivo_id
                  )
                ORDER BY l.archivo_id
                """,
                (dia_carga,),
            ).fetchall()
        ]
        return [
            _procesar(
                conexion,
                rutas,
                archivo,
                dia_carga=dia_carga,
                lotes=lotes,
                extractor=extractor,
                ahora=ahora,
            )
            for archivo in pendientes
        ]
    finally:
        conexion.close()
