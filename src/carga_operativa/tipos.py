"""Tipos compartidos, sin lógica. No importa ningún otro módulo del paquete."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Formula:
    """Una celda de Excel con fórmula. Se transporta; nunca se evalúa."""

    texto: str


@dataclass(frozen=True)
class FilaCruda:
    """Una fila tal como la emitió un lector o un extractor: sin tipar."""

    n_fila: int
    campos: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CorreoTexto:
    """Lo único que ve un extractor: asunto y cuerpo de texto plano."""

    asunto: str
    cuerpo: str


class RechazoArchivo(Exception):
    """El archivo completo no entra. Lleva el motivo y los campos de la plantilla.

    El motivo es un código de la lista cerrada; el texto del detalle lo arma
    ``contrato.motivos.redactar``. Nunca viaja aquí el mensaje de otra excepción.
    """

    def __init__(self, motivo: str, **campos: object) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.campos = campos
