"""Confinamiento de rutas. Solo usa ``pathlib`` y ``os``."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


class RutaNoConfinada(ValueError):
    """La ruta pedida no queda dentro de la raíz permitida."""


def resolver_confinada(raiz: Path, relativa: str) -> Path:
    """Devuelve ``raiz / relativa`` solo si queda dentro de ``raiz``.

    Rechaza rutas absolutas, cualquier componente ``..``, enlaces simbólicos en
    el camino y todo lo que, ya resuelto, caiga fuera de la raíz.
    """
    if not isinstance(relativa, str) or not relativa or "\x00" in relativa:
        raise RutaNoConfinada("La ruta relativa no es un texto válido.")
    if os.path.isabs(relativa) or relativa.startswith(("/", "\\")):
        raise RutaNoConfinada("No se admiten rutas absolutas.")
    if PureWindowsPath(relativa).drive:
        raise RutaNoConfinada("No se admiten rutas con unidad.")
    partes = [parte for parte in relativa.replace("\\", "/").split("/") if parte not in ("", ".")]
    if not partes or ".." in partes:
        raise RutaNoConfinada("No se admite subir de directorio.")

    raiz_real = Path(os.path.realpath(raiz))
    actual = raiz_real
    for parte in partes:
        actual = actual / parte
        if actual.is_symlink():
            raise RutaNoConfinada("No se admiten enlaces simbólicos.")

    resuelta = Path(os.path.realpath(actual))
    if raiz_real not in resuelta.parents:
        raise RutaNoConfinada("La ruta queda fuera de la raíz.")
    return actual
