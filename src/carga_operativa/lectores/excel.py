"""Lector de Excel: xlsx a filas crudas.

Antes de abrir el libro revisa el zip. Un libro con macros se rechaza entero.
El libro se abre con ``data_only=False``: una celda con fórmula llega como
``Formula`` y no se evalúa ni se usa el valor que dejó calculado quien lo envió.
"""

from __future__ import annotations

import io
import logging
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any

from openpyxl import load_workbook

from ..config import MAX_DESCOMPRIMIDO
from ..tipos import FilaCruda, Formula, RechazoArchivo

_registro = logging.getLogger(__name__)

_TIPOS_CONTENIDO = "[Content_Types].xml"
_MACROS = "vbaproject.bin"
_MARCAS_MACRO = (b"macroEnabled", b"vbaProject")
_SUFIJOS_XML = (".xml", ".rels", ".vml")
_MARCAS_DTD = tuple(
    marca.encode(codificacion)
    for marca in ("<!DOCTYPE", "<!ENTITY")
    for codificacion in ("utf-8", "utf-16-le", "utf-16-be")
)
_ERRORES_ZIP = (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError)


def _revisar_zip(datos: bytes) -> None:
    """Rechaza macros, tamaños excesivos y XML interno con DTD, sin abrir el libro."""
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as archivo:
            entradas = archivo.infolist()
            if any(PurePosixPath(e.filename).name.lower() == _MACROS for e in entradas):
                raise RechazoArchivo("excel_con_macros")
            if sum(e.file_size for e in entradas) > MAX_DESCOMPRIMIDO:
                raise RechazoArchivo("archivo_demasiado_grande")
            nombres = {e.filename for e in entradas}
            if _TIPOS_CONTENIDO not in nombres:
                raise RechazoArchivo("archivo_ilegible", causa="excel_invalido")
            if any(marca in archivo.read(_TIPOS_CONTENIDO) for marca in _MARCAS_MACRO):
                raise RechazoArchivo("excel_con_macros")
            for entrada in entradas:
                if not entrada.filename.lower().endswith(_SUFIJOS_XML):
                    continue
                contenido = archivo.read(entrada)
                if any(marca in contenido for marca in _MARCAS_DTD):
                    raise RechazoArchivo("archivo_ilegible", causa="xml_interno_con_dtd")
    except _ERRORES_ZIP:
        _registro.warning("Zip de Excel ilegible.", exc_info=True)
        raise RechazoArchivo("archivo_ilegible", causa="zip_invalido") from None


def _vacio(valor: object) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _valor(celda: object) -> object:
    valor = getattr(celda, "value", None)
    if getattr(celda, "data_type", None) == "f":
        return Formula(valor if isinstance(valor, str) else "(fórmula matricial)")
    if valor is None or isinstance(valor, str | int | float | bool):
        return valor
    if hasattr(valor, "isoformat"):
        return valor
    # Cualquier otro objeto de la librería (fórmulas matriciales, tablas de
    # datos) se trata como fórmula: no es un valor.
    return Formula("(fórmula)")


def _encabezados(fila: list[object]) -> list[str | None]:
    nombres: list[str | None] = []
    for indice, valor in enumerate(fila, start=1):
        if _vacio(valor):
            nombres.append(None)
        elif isinstance(valor, str):
            nombres.append(valor.strip())
        else:
            # Un encabezado que no es texto no se interpreta: queda como campo
            # desconocido y el contrato lo rechaza.
            nombres.append(f"columna_{indice}")
    while nombres and nombres[-1] is None:
        nombres.pop()
    presentes = [nombre for nombre in nombres if nombre is not None]
    if len(presentes) != len(set(presentes)):
        raise RechazoArchivo("archivo_ilegible", causa="encabezado_repetido")
    return nombres


def _filas(hoja: Any) -> list[FilaCruda]:
    encabezados: list[str | None] | None = None
    filas: list[FilaCruda] = []
    for n_fila, celdas in enumerate(hoja.iter_rows(), start=1):
        valores = [_valor(celda) for celda in celdas]
        if all(_vacio(valor) for valor in valores):
            # Una fila con todas las celdas vacías no es una fila.
            continue
        if encabezados is None:
            encabezados = _encabezados(valores)
            continue
        campos: dict[str, object] = {nombre: None for nombre in encabezados if nombre}
        for indice, valor in enumerate(valores):
            nombre = encabezados[indice] if indice < len(encabezados) else None
            if nombre is not None:
                campos[nombre] = valor
            elif not _vacio(valor):
                # Dato bajo una columna sin encabezado: no se descarta.
                campos[f"columna_{indice + 1}"] = valor
        filas.append(FilaCruda(n_fila=n_fila, campos=campos))
    return filas


def leer(datos: bytes) -> list[FilaCruda]:
    """Filas crudas de la única hoja del libro. La primera fila con datos es el encabezado."""
    _revisar_zip(datos)
    try:
        libro = load_workbook(
            io.BytesIO(datos), read_only=True, data_only=False, keep_vba=False, keep_links=False
        )
        try:
            if len(libro.worksheets) != 1 or len(libro.sheetnames) != 1:
                raise RechazoArchivo("archivo_ilegible", causa="varias_hojas")
            return _filas(libro.worksheets[0])
        finally:
            libro.close()
    except RechazoArchivo:
        raise
    except Exception:  # noqa: BLE001
        # openpyxl falla de muchas formas ante un libro mal formado. La traza va
        # al registro técnico; a la base llega solo el motivo.
        _registro.warning("Libro de Excel ilegible.", exc_info=True)
        raise RechazoArchivo("archivo_ilegible", causa="excel_invalido") from None
