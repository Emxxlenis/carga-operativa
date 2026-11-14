"""Dependencias con versión fijada y con huella. Nada de servicios obligatorios."""

from __future__ import annotations

import re
import sqlite3
import sys
import tomllib
from importlib import metadata

from conftest import RAIZ_REPO

FIJADA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][A-Za-z0-9.]*$")


def _proyecto() -> dict:
    return tomllib.loads((RAIZ_REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _requisitos_del_lock() -> dict[str, tuple[str, int]]:
    """nombre -> (versión, cantidad de huellas)."""
    texto = (RAIZ_REPO / "requirements.lock").read_text(encoding="utf-8")
    logicas = texto.replace("\\\n", " ").splitlines()
    requisitos: dict[str, tuple[str, int]] = {}
    for linea in logicas:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        especificacion = linea.split(";")[0].split("--hash")[0].strip()
        assert FIJADA.fullmatch(especificacion), f"sin versión exacta: {especificacion!r}"
        nombre, version = especificacion.split("==")
        huellas = re.findall(r"--hash=sha256:[0-9a-f]{64}\b", linea)
        requisitos[nombre.lower()] = (version, len(huellas))
    return requisitos


def test_dependencias_fijadas() -> None:
    proyecto = _proyecto()
    directas = proyecto["project"]["dependencies"]
    desarrollo = proyecto["project"]["optional-dependencies"]["dev"]

    assert directas == ["openpyxl==3.1.5", "defusedxml==0.7.1"], (
        "solo dos dependencias de ejecución"
    )
    for requisito in (*directas, *desarrollo, *proyecto["build-system"]["requires"]):
        assert FIJADA.fullmatch(requisito), f"sin versión exacta: {requisito}"

    lock = _requisitos_del_lock()
    assert len(lock) >= len(directas) + len(desarrollo)
    for nombre, (_version, huellas) in lock.items():
        assert huellas >= 1, f"{nombre} no tiene --hash en requirements.lock"
    for requisito in (*directas, *desarrollo):
        nombre, version = requisito.split("==")
        assert lock[nombre.lower()][0] == version, f"{nombre}: el lock y pyproject difieren"

    crudo = (RAIZ_REPO / "requirements.lock").read_text(encoding="utf-8")
    for prohibido in ("git+", "http://", "https://", "--index-url", "--find-links", " @ ", "-e "):
        assert prohibido not in crudo, prohibido


def test_lo_instalado_es_lo_fijado() -> None:
    lock = _requisitos_del_lock()
    for nombre in ("openpyxl", "defusedxml", "pytest"):
        assert metadata.version(nombre) == lock[nombre][0], nombre


def test_version_de_python_y_de_sqlite_admitidas() -> None:
    assert _proyecto()["project"]["requires-python"] == ">=3.11,<3.13"
    assert sys.version_info[:2] in ((3, 11), (3, 12))
    assert sqlite3.sqlite_version_info >= (3, 37), "las tablas STRICT requieren SQLite 3.37"


def test_openpyxl_usa_el_analizador_endurecido() -> None:
    """Con defusedxml instalado, openpyxl lo usa para el XML interno del libro."""
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML is True
    assert openpyxl.xml.LXML is False, "lxml cambiaría el analizador que revisa el libro"


def test_el_paquete_no_tiene_mas_dependencias_que_las_declaradas() -> None:
    """Todo import de terceros del paquete es openpyxl o defusedxml."""
    import ast

    terceros: set[str] = set()
    for ruta in (RAIZ_REPO / "src" / "carga_operativa").rglob("*.py"):
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            nombres: list[str] = []
            if isinstance(nodo, ast.Import):
                nombres = [alias.name for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
                nombres = [nodo.module]
            for nombre in nombres:
                raiz = nombre.split(".")[0]
                if raiz not in sys.stdlib_module_names and raiz != "carga_operativa":
                    terceros.add(raiz)
    assert terceros == {"openpyxl", "defusedxml"}
