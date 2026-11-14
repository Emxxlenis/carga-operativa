"""Puerto del extractor: texto de un correo a registros crudos.

La salida es ``object`` a propósito. No es confiable: el contrato comprueba la
forma y valida cada registro con las mismas reglas que Excel y XML.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..tipos import CorreoTexto


class ExtraccionNoDisponible(Exception):
    """El extractor no pudo responder. El archivo queda pendiente."""


@runtime_checkable
class Extractor(Protocol):
    nombre: str  # 'determinista' | 'api'

    def extraer(self, correo: CorreoTexto) -> object: ...
