"""Configuración común: red bloqueada, reloj fijo, base temporal y buzón de prueba."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from carga_operativa import pipeline
from carga_operativa.config import VARIABLE_CLAVE, VARIABLE_MODELO, VARIABLE_URL, Rutas
from carga_operativa.db import conexion
from carga_operativa.extraccion.determinista import ExtractorDeterminista
from dobles import DIA, Buzon, reloj_fijo

RAIZ_REPO = Path(__file__).resolve().parents[1]


class RedBloqueada(RuntimeError):
    """Un test intentó abrir una conexión de red."""


@pytest.fixture(scope="session", autouse=True)
def sin_red() -> Iterator[list[str]]:
    """Toda la suite corre sin red: abrir un socket es un error."""
    intentos: list[str] = []

    class SocketBloqueado(socket.socket):
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            intentos.append("socket")
            raise RedBloqueada("Los tests no usan la red.")

    def bloqueado(*_args: object, **_kwargs: object) -> None:
        intentos.append("conexion")
        raise RedBloqueada("Los tests no usan la red.")

    parche = pytest.MonkeyPatch()
    parche.setattr(socket, "socket", SocketBloqueado)
    parche.setattr(socket, "create_connection", bloqueado)
    parche.setattr(socket, "getaddrinfo", bloqueado)
    yield intentos
    parche.undo()
    if intentos:
        pytest.fail(f"La suite intentó usar la red {len(intentos)} veces.", pytrace=False)


@pytest.fixture(autouse=True)
def sin_clave_de_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test hereda una clave real del entorno de quien corre la suite."""
    for variable in (VARIABLE_CLAVE, VARIABLE_URL, VARIABLE_MODELO):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def rutas(tmp_path: Path) -> Rutas:
    return Rutas(raiz=tmp_path / "datos")


@pytest.fixture
def base(rutas: Rutas) -> Rutas:
    """Rutas con la base ya inicializada y vacía."""
    conexion.inicializar(rutas.base)
    return rutas


@pytest.fixture
def buzon(rutas: Rutas) -> Buzon:
    return Buzon(rutas)


@pytest.fixture
def correr(rutas: Rutas) -> Callable[..., pipeline.ResultadoDia]:
    """Corre un día con el extractor determinista y el reloj fijo."""

    def _correr(dia: str = DIA, extractor: object | None = None) -> pipeline.ResultadoDia:
        return pipeline.dia(
            rutas, dia, extractor=extractor or ExtractorDeterminista(), reloj=reloj_fijo
        )

    return _correr


@pytest.fixture(scope="session")
def demo_generada(tmp_path_factory: pytest.TempPathFactory) -> tuple[Rutas, Path, list]:
    """La demo completa, generada una vez por sesión en un directorio temporal."""
    raiz = tmp_path_factory.mktemp("demo")
    rutas = Rutas(raiz=raiz / "datos")
    destino = raiz / "docs"
    resultados = pipeline.demo(rutas, destino, extractor=ExtractorDeterminista(), reloj=reloj_fijo)
    return rutas, destino, resultados
