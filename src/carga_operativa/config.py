"""Constantes y rutas. Lee el entorno solo para elegir el extractor.

No importa ningún otro módulo del paquete.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Datos de demostración: todo sale de esta semilla.
SEMILLA = 20261112
DIA_DEMO = "2026-11-12"
DIAS_HISTORIA = 35

# Límites de entrada.
MAX_BYTES = 2 * 1024 * 1024
MAX_DESCOMPRIMIDO = 16 * 1024 * 1024
MAX_CUERPO_CORREO = 20_000

# Extractor opcional. Sin clave se usa el determinista.
VARIABLE_CLAVE = "CARGA_EXTRACTOR_API_KEY"
VARIABLE_URL = "CARGA_EXTRACTOR_API_URL"
VARIABLE_MODELO = "CARGA_EXTRACTOR_API_MODELO"
TIEMPO_LIMITE_API = 20.0

EXTRACTOR_DETERMINISTA = "determinista"
EXTRACTOR_API = "api"


@dataclass(frozen=True)
class Rutas:
    """Rutas de trabajo, todas bajo una sola raíz de datos."""

    raiz: Path

    @property
    def buzon(self) -> Path:
        return self.raiz / "buzon"

    @property
    def landing(self) -> Path:
        return self.raiz / "landing"

    @property
    def base(self) -> Path:
        return self.raiz / "carga.db"

    @property
    def registro(self) -> Path:
        return self.raiz / "carga.log"


class ConfiguracionInvalida(ValueError):
    """La configuración del extractor opcional está incompleta."""


@dataclass(frozen=True)
class EleccionExtractor:
    """Qué extractor usar. La clave nunca aparece en repr()."""

    nombre: str
    clave: str = field(default="", repr=False)
    url: str = ""
    modelo: str = ""


def elegir_extractor(entorno: Mapping[str, str] | None = None) -> EleccionExtractor:
    """Sin clave de API se usa el extractor determinista.

    Con clave se exige también la dirección (https) y el modelo. Una
    configuración a medias es un error explícito, no un recambio silencioso.
    """
    valores = os.environ if entorno is None else entorno
    clave = valores.get(VARIABLE_CLAVE, "").strip()
    if not clave:
        return EleccionExtractor(nombre=EXTRACTOR_DETERMINISTA)
    url = valores.get(VARIABLE_URL, "").strip()
    modelo = valores.get(VARIABLE_MODELO, "").strip()
    if not url.lower().startswith("https://"):
        raise ConfiguracionInvalida(
            f"Con {VARIABLE_CLAVE} definida, {VARIABLE_URL} debe ser https."
        )
    if not modelo:
        raise ConfiguracionInvalida(f"Con {VARIABLE_CLAVE} definida, falta {VARIABLE_MODELO}.")
    return EleccionExtractor(nombre=EXTRACTOR_API, clave=clave, url=url, modelo=modelo)
