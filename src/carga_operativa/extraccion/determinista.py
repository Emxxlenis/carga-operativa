"""Extractor determinista de demostración. Se usa cuando no hay clave de API.

Gramática por líneas:

- ``Fecha: AAAA-MM-DD`` fija la fecha de las líneas siguientes.
- ``<métrica> lote <código>: <valor> <unidad>`` es un registro.

Toda línea que mencione ``lote`` emite un registro con las claves que se
pudieron leer. Si le falta algo sale incompleta y la rechaza el contrato; aquí
no se descarta nada ni se completa nada. No resuelve fechas relativas.
"""

from __future__ import annotations

import re

from ..tipos import CorreoTexto

_FECHA = re.compile(r"^\s*fecha\s*:\s*(?P<fecha>\S+)\s*$", re.IGNORECASE)
_MENCIONA_LOTE = re.compile(r"\blote\b", re.IGNORECASE)
_REGISTRO = re.compile(
    r"^\s*(?P<metrica>\S+)\s+lote\s+(?P<lote>[^\s:]+)\s*:"
    r"\s*(?P<valor>\S+)(?:\s+(?P<unidad>\S+))?\s*$",
    re.IGNORECASE,
)


class ExtractorDeterminista:
    nombre = "determinista"

    def extraer(self, correo: CorreoTexto) -> object:
        registros: list[dict[str, object]] = []
        fecha: str | None = None
        for linea in correo.cuerpo.splitlines():
            encontrada = _FECHA.match(linea)
            if encontrada:
                fecha = encontrada.group("fecha")
                continue
            if not _MENCIONA_LOTE.search(linea):
                continue
            registro: dict[str, object] = {}
            if fecha is not None:
                registro["fecha"] = fecha
            partes = _REGISTRO.match(linea)
            if partes:
                registro.update(
                    {clave: valor for clave, valor in partes.groupdict().items() if valor}
                )
            registros.append(registro)
        return registros
