"""Conexiones por capa. Cada una lleva un autorizador de SQLite.

El autorizador se consulta al preparar cada sentencia. Lo que la tabla de
permisos no concede se deniega y sale como ``AccesoCapaProhibido``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from .permisos import FUNCIONES_PERMITIDAS, LECTURAS_DE_TRIGGER, PERMISOS, Permiso

SQLITE_MINIMO = (3, 37, 0)  # primera versión con tablas STRICT

# Las capas que no pueden importar pathlib anotan la ruta de la base con esto.
RutaBase = Path

_ACCIONES_LIBRES = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_RECURSIVE,
    }
)

_NOMBRES = {
    sqlite3.SQLITE_READ: "leer",
    sqlite3.SQLITE_INSERT: "insertar",
    sqlite3.SQLITE_UPDATE: "actualizar",
    sqlite3.SQLITE_DELETE: "borrar",
    sqlite3.SQLITE_FUNCTION: "funcion",
    sqlite3.SQLITE_PRAGMA: "pragma",
    sqlite3.SQLITE_ATTACH: "attach",
}


class AccesoCapaProhibido(Exception):
    """Una capa intentó algo que su tabla de permisos no concede."""

    def __init__(self, capa: str, accion: str, objeto: str) -> None:
        super().__init__(f"La capa '{capa}' no puede {accion} '{objeto}'.")
        self.capa = capa
        self.accion = accion
        self.objeto = objeto


class SqliteSinStrict(RuntimeError):
    """El SQLite del intérprete no soporta tablas STRICT."""


def _permitido(
    permiso: Permiso,
    accion: int,
    arg1: str | None,
    arg2: str | None,
    origen: str | None,
) -> bool:
    if accion in _ACCIONES_LIBRES:
        return True
    if accion == sqlite3.SQLITE_FUNCTION:
        return (arg2 or "").lower() in FUNCIONES_PERMITIDAS
    if accion == sqlite3.SQLITE_READ:
        if arg1 in permiso.lee:
            return True
        return (origen, arg1, arg2) in LECTURAS_DE_TRIGGER
    if permiso.solo_lectura:
        return False
    if accion == sqlite3.SQLITE_INSERT:
        return arg1 in permiso.inserta
    if accion == sqlite3.SQLITE_UPDATE:
        return arg1 in permiso.actualiza
    if accion == sqlite3.SQLITE_DELETE:
        return arg1 in permiso.borra
    # PRAGMA, ATTACH, DETACH, DDL y todo lo demás.
    return False


class _CursorCapa(sqlite3.Cursor):
    def execute(self, sql: str, parametros: Iterable[object] = (), /) -> _CursorCapa:
        conexion: Any = self.connection
        conexion._denegado = None
        try:
            super().execute(sql, parametros)
        except sqlite3.DatabaseError as error:
            conexion._traducir(error)
            raise
        return self

    def executemany(self, sql: str, filas: Iterable[Iterable[object]], /) -> _CursorCapa:
        conexion: Any = self.connection
        conexion._denegado = None
        try:
            super().executemany(sql, filas)
        except sqlite3.DatabaseError as error:
            conexion._traducir(error)
            raise
        return self

    def executescript(self, sql: str, /) -> _CursorCapa:
        conexion: Any = self.connection
        raise AccesoCapaProhibido(conexion.capa, "ejecutar", "executescript")


class ConexionCapa(sqlite3.Connection):
    """Conexión de una capa. Toda sentencia pasa por el cursor que traduce."""

    capa: str = ""
    _denegado: tuple[str, str] | None = None

    def cursor(self, factory: type[sqlite3.Cursor] | None = None) -> sqlite3.Cursor:
        # Siempre el cursor que traduce, sin importar la fábrica pedida.
        return super().cursor(_CursorCapa)

    def execute(self, sql: str, parametros: Iterable[object] = (), /) -> sqlite3.Cursor:
        return self.cursor().execute(sql, parametros)

    def executemany(self, sql: str, filas: Iterable[Iterable[object]], /) -> sqlite3.Cursor:
        return self.cursor().executemany(sql, filas)

    def executescript(self, sql: str, /) -> sqlite3.Cursor:
        raise AccesoCapaProhibido(self.capa, "ejecutar", "executescript")

    def _traducir(self, error: sqlite3.DatabaseError) -> None:
        if self._denegado is not None:
            accion, objeto = self._denegado
            self._denegado = None
            raise AccesoCapaProhibido(self.capa, accion, objeto) from error


def _instalar_autorizador(conexion: ConexionCapa, permiso: Permiso) -> None:
    def decidir(
        accion: int,
        arg1: str | None,
        arg2: str | None,
        _base: str | None,
        origen: str | None,
    ) -> int:
        if _permitido(permiso, accion, arg1, arg2, origen):
            return sqlite3.SQLITE_OK
        conexion._denegado = (_NOMBRES.get(accion, f"accion {accion}"), arg1 or arg2 or "")
        return sqlite3.SQLITE_DENY

    conexion.set_authorizer(decidir)


def inicializar(ruta_base: Path) -> None:
    """Crea la base y aplica el esquema. Es idempotente."""
    if sqlite3.sqlite_version_info < SQLITE_MINIMO:
        raise SqliteSinStrict(
            f"Se requiere SQLite 3.37 o superior para tablas STRICT; hay {sqlite3.sqlite_version}."
        )
    ruta = Path(ruta_base)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    esquema = resources.files(__package__).joinpath("esquema.sql").read_text(encoding="utf-8")
    conexion = sqlite3.connect(ruta)
    try:
        conexion.executescript(esquema)
        conexion.commit()
    finally:
        conexion.close()


def abrir(ruta_base: Path, capa: str) -> ConexionCapa:
    """Abre la base para una capa, con su autorizador ya instalado."""
    try:
        permiso = PERMISOS[capa]
    except KeyError:
        raise ValueError(f"Capa desconocida: {capa}") from None
    ruta = Path(ruta_base)
    if not ruta.is_file():
        raise FileNotFoundError("La base no existe. Hay que inicializarla antes de abrir una capa.")
    if permiso.solo_lectura:
        conexion = sqlite3.connect(
            f"{ruta.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
            factory=ConexionCapa,
        )
    else:
        conexion = sqlite3.connect(ruta, isolation_level=None, factory=ConexionCapa)
    conexion.capa = capa
    # Los PRAGMA van antes del autorizador; después quedan denegados.
    # recursive_triggers hace que un INSERT OR REPLACE dispare los triggers de
    # DELETE: sin él, REPLACE pisaría una fila solo-inserción sin avisar.
    sqlite3.Connection.execute(conexion, "PRAGMA foreign_keys = ON")
    sqlite3.Connection.execute(conexion, "PRAGMA recursive_triggers = ON")
    conexion.create_function("capa_actual", 0, lambda: capa)
    _instalar_autorizador(conexion, permiso)
    return conexion


@contextmanager
def transaccion(conexion: ConexionCapa) -> Iterator[ConexionCapa]:
    """Una transacción explícita: confirma al salir bien, revierte si no."""
    conexion.execute("BEGIN IMMEDIATE")
    try:
        yield conexion
    except BaseException:
        if conexion.in_transaction:
            conexion.execute("ROLLBACK")
        raise
    conexion.execute("COMMIT")
