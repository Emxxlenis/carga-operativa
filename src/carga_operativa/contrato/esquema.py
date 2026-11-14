"""Definición del contrato de una fila: campos, métricas, unidades y límites."""

from __future__ import annotations

import re

VERSION_CONTRATO = "1"

# Una fila tiene exactamente estas cinco claves.
CAMPOS: tuple[str, ...] = ("fecha", "lote", "metrica", "valor", "unidad")

METRICAS: tuple[str, ...] = ("produccion", "mortalidad", "ventas")

# No hay conversión de unidades.
UNIDAD_POR_METRICA: dict[str, str] = {
    "produccion": "kg",
    "mortalidad": "aves",
    "ventas": "kg",
}

# Máximo por fila (un lote, un día). Son límites de demostración.
MAXIMOS: dict[str, float] = {
    "produccion": 50_000.0,
    "mortalidad": 5_000.0,
    "ventas": 50_000.0,
}

METRICA_ENTERA = "mortalidad"

PATRON_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
# Sin signo, sin coma decimal, sin separador de miles.
PATRON_VALOR = re.compile(r"\d+(\.\d{1,3})?")
