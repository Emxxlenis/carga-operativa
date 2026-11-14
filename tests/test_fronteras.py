"""Fronteras entre capas: permisos dentro de SQLite e imports entre módulos."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from carga_operativa import cuarentena
from carga_operativa.config import Rutas
from carga_operativa.db import conexion
from carga_operativa.db.conexion import AccesoCapaProhibido, abrir
from carga_operativa.db.permisos import PERMISOS, TABLAS
from dobles import AHORA, DIA

PAQUETE = "carga_operativa"
FUENTES = Path(__file__).resolve().parents[1] / "src" / PAQUETE

MART = {"mart_produccion", "mart_mortalidad", "mart_ventas", "mart_resumen_dia"}
MODELO = {"dim_fecha", "dim_lote", "hecho_dia_lote"}
CONTRATO = {"ref_lote", "contrato_archivo", "contrato_fila_aceptada"}
CONCILIACION = {"conciliacion_dia", "conciliacion_control"}

# Copia a mano de la tabla del diseño. Si alguien afloja permisos.py, esto falla.
LEE_CAPA_ANTERIOR = {
    "landing": set(),
    "contrato": {"landing_archivo"},
    "modelo": {"contrato_fila_aceptada", "ref_lote"},
    "mart": MODELO,
    "conciliacion": set(TABLAS) - CONCILIACION,
    "pronostico": {"mart_resumen_dia", "conciliacion_dia"},
    "demo": MART | CONCILIACION | {"pronostico_linea_base", "cuarentena"},
}
# Tablas propias que la capa necesita leer para ON CONFLICT, claves foráneas y DELETE.
LEE_PROPIAS = {
    "landing": {"landing_archivo"},
    "contrato": CONTRATO,
    "modelo": MODELO,
    "mart": MART,
    "conciliacion": CONCILIACION,
    "pronostico": {"pronostico_linea_base"},
    "demo": set(),
}
INSERTA = {
    "landing": {"landing_archivo", "cuarentena"},
    "contrato": CONTRATO | {"cuarentena"},
    "modelo": MODELO,
    "mart": MART,
    "conciliacion": CONCILIACION,
    "pronostico": {"pronostico_linea_base"},
    "demo": set(),
}
ACTUALIZA = {
    "landing": set(),
    "contrato": set(),
    "modelo": MODELO,
    "mart": MART,
    "conciliacion": CONCILIACION,
    "pronostico": set(),
    "demo": set(),
}
BORRA = {capa: set() for capa in INSERTA} | {"pronostico": {"pronostico_linea_base"}}


def _prohibido(con: conexion.ConexionCapa, sql: str) -> bool:
    try:
        con.execute(sql)
    except AccesoCapaProhibido:
        return True
    except sqlite3.DatabaseError:
        # Autorizado, pero la sentencia falló por otra razón (restricción, trigger).
        return False
    return False


def test_la_tabla_de_permisos_cubre_las_capas_del_diseno() -> None:
    assert set(PERMISOS) == set(LEE_CAPA_ANTERIOR)


@pytest.mark.parametrize("capa", sorted(LEE_CAPA_ANTERIOR))
def test_cada_capa_solo_lee_la_anterior(base: Rutas, capa: str) -> None:
    permitidas = LEE_CAPA_ANTERIOR[capa] | LEE_PROPIAS[capa]
    con = abrir(base.base, capa)
    try:
        for tabla in sorted(TABLAS):
            if tabla in permitidas:
                assert con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone() == (0,)
            else:
                with pytest.raises(AccesoCapaProhibido):
                    con.execute(f"SELECT * FROM {tabla}")
        with pytest.raises(AccesoCapaProhibido):
            con.execute("SELECT name FROM sqlite_master")
    finally:
        con.close()


@pytest.mark.parametrize("capa", sorted(INSERTA))
def test_cada_capa_solo_escribe_lo_suyo(base: Rutas, capa: str) -> None:
    con = abrir(base.base, capa)
    try:
        for tabla in sorted(TABLAS):
            columna = con_columna(base, tabla)
            assert _prohibido(con, f"INSERT INTO {tabla} DEFAULT VALUES") == (
                tabla not in INSERTA[capa]
            ), f"insertar en {tabla}"
            assert _prohibido(con, f"UPDATE {tabla} SET {columna} = {columna}") == (
                tabla not in ACTUALIZA[capa]
            ), f"actualizar {tabla}"
            assert _prohibido(con, f"DELETE FROM {tabla}") == (tabla not in BORRA[capa]), (
                f"borrar de {tabla}"
            )
    finally:
        con.close()


def con_columna(rutas: Rutas, tabla: str) -> str:
    cruda = sqlite3.connect(rutas.base)
    try:
        return cruda.execute(f"SELECT name FROM pragma_table_info('{tabla}')").fetchone()[0]
    finally:
        cruda.close()


@pytest.mark.parametrize("capa", sorted(PERMISOS))
@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA foreign_keys = OFF",
        "PRAGMA recursive_triggers = OFF",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE ':memory:' AS otra",
        "CREATE TABLE intrusa (x)",
        "DROP TABLE cuarentena",
        "DROP TRIGGER cuarentena_sin_delete",
        "CREATE VIEW espia AS SELECT * FROM landing_archivo",
        "SELECT load_extension('x')",
    ],
)
def test_pragma_attach_y_ddl_estan_denegados(base: Rutas, capa: str, sql: str) -> None:
    con = abrir(base.base, capa)
    try:
        with pytest.raises(AccesoCapaProhibido):
            con.execute(sql)
        with pytest.raises(AccesoCapaProhibido):
            con.executescript(sql)
    finally:
        con.close()


def test_una_subconsulta_con_alias_no_esquiva_la_frontera(base: Rutas) -> None:
    con = abrir(base.base, "pronostico")
    try:
        with pytest.raises(AccesoCapaProhibido):
            con.execute("WITH c AS (SELECT sha256 FROM landing_archivo) SELECT * FROM c")
        with pytest.raises(AccesoCapaProhibido):
            con.execute("SELECT * FROM (SELECT valor FROM contrato_fila_aceptada) AS t")
        with pytest.raises(AccesoCapaProhibido):
            con.cursor().execute("SELECT motivo FROM cuarentena")
    finally:
        con.close()


def test_la_conexion_de_demo_es_de_solo_lectura(base: Rutas) -> None:
    con = abrir(base.base, "demo")
    try:
        with pytest.raises(AccesoCapaProhibido):
            con.execute("INSERT INTO mart_resumen_dia DEFAULT VALUES")
    finally:
        con.close()
    # Aun sin autorizador, el archivo está abierto en modo solo lectura.
    con = abrir(base.base, "demo")
    try:
        con.set_authorizer(None)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute(
                "INSERT INTO conciliacion_dia (dia_carga, estado, calculado_en) "
                "VALUES ('2026-01-01', 'cuadra', 'x')"
            )
    finally:
        con.close()


def test_landing_solo_registra_entrada_no_regular(base: Rutas) -> None:
    con = abrir(base.base, "landing")
    try:
        con.execute(
            "INSERT INTO landing_archivo "
            "(sha256, dia_carga, nombre_original, ruta_relativa, formato, bytes, recibido_en) "
            "VALUES ('a', ?, 'a.xml', 'a', 'xml', 1, ?)",
            (DIA, AHORA),
        )
        with pytest.raises(sqlite3.IntegrityError, match="landing solo registra"):
            cuarentena.registrar(
                con,
                dia_carga=DIA,
                nombre_archivo="a.xml",
                archivo_id=1,
                n_fila=0,
                motivo="archivo_ilegible",
                campos={},
                fila_cruda=None,
                ahora=AHORA,
            )
        cuarentena.registrar(
            con,
            dia_carga=DIA,
            nombre_archivo="enlace",
            archivo_id=None,
            n_fila=0,
            motivo="entrada_no_regular",
            campos={},
            fila_cruda=None,
            ahora=AHORA,
        )
    finally:
        con.close()


# --- Imports -----------------------------------------------------------------

RED = (
    "socket",
    "ssl",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "webbrowser",
    "asyncio",
)
LECTORES_BASE = ("sqlite3", "subprocess", *RED)

# módulo -> (subpaquetes internos prohibidos, módulos externos prohibidos)
PROHIBIDOS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "landing.recepcion": (
        ("lectores", "extraccion", "contrato", "modelo", "mart", "pronostico", "demo"),
        ("openpyxl", "defusedxml"),
    ),
    "lectores.excel": (("db", "contrato", "modelo", "mart", "pronostico"), LECTORES_BASE),
    "lectores.xml_seguro": (
        ("db", "contrato", "modelo", "mart", "pronostico"),
        (*LECTORES_BASE, "xml", "lxml"),
    ),
    "lectores.correo": (
        ("db", "contrato", "modelo", "mart", "pronostico"),
        (*LECTORES_BASE, "html"),
    ),
    "extraccion.puerto": (("db", "landing", "lectores", "contrato"), ("pathlib", "os", *RED)),
    "extraccion.determinista": (
        ("db", "landing", "lectores", "contrato"),
        ("pathlib", "os", *RED),
    ),
    "extraccion.api_opcional": (
        ("db", "landing", "contrato", "modelo", "mart", "pronostico"),
        (),
    ),
    "contrato.esquema": (
        ("db", "landing", "lectores", "extraccion"),
        ("sqlite3", "os", "pathlib", "openpyxl", "defusedxml"),
    ),
    "contrato.motivos": (
        ("db", "landing", "lectores", "extraccion"),
        ("sqlite3", "os", "pathlib", "openpyxl", "defusedxml"),
    ),
    "contrato.reglas": (
        ("db", "landing", "lectores", "extraccion"),
        ("sqlite3", "os", "pathlib", "openpyxl", "defusedxml"),
    ),
    "contrato.aplicar": (("modelo", "mart", "pronostico", "demo", "conciliacion"), ()),
    "modelo.construir": (
        ("landing", "lectores", "extraccion", "contrato", "cuarentena"),
        ("pathlib", "openpyxl", "defusedxml"),
    ),
    "pronostico.linea_base": (
        ("landing", "lectores", "extraccion", "contrato", "cuarentena", "modelo", "mart"),
        ("pathlib", "os", "numpy", "pandas", "sklearn", "statsmodels"),
    ),
    "demo.exportar": (("landing", "lectores", "extraccion", "modelo"), RED),
    "sintetico.generar": (
        (
            "db",
            "pipeline",
            "landing",
            "lectores",
            "extraccion",
            "contrato",
            "cuarentena",
            "modelo",
            "mart",
            "conciliacion",
            "pronostico",
            "demo",
        ),
        ("time", *RED),
    ),
}

# módulo -> únicos módulos internos que puede importar
SOLO_INTERNOS: dict[str, tuple[str, ...]] = {
    "config": (),
    "tipos": (),
    "db.permisos": ("db",),
    "db.conexion": ("db",),
    "landing.rutas": (),
    "cuarentena": ("db", "contrato.motivos"),
    "mart.construir": ("db",),
    "conciliacion": ("db",),
}

# módulo -> únicos módulos externos que puede importar
SOLO_EXTERNOS: dict[str, tuple[str, ...]] = {"landing.rutas": ("pathlib", "os", "__future__")}

SIN_OPEN = ("modelo.construir", "pronostico.linea_base")
UNICO_CON_RED = "extraccion.api_opcional"


def _modulos() -> dict[str, Path]:
    modulos = {}
    for ruta in sorted(FUENTES.rglob("*.py")):
        partes = ruta.relative_to(FUENTES).with_suffix("").parts
        if partes[-1] == "__init__":
            partes = partes[:-1]
        modulos[".".join(partes)] = ruta
    return modulos


def _imports(modulo: str, ruta: Path) -> set[str]:
    """Nombres absolutos de todo lo que el módulo importa."""
    es_paquete = ruta.name == "__init__.py"
    paquete = [PAQUETE, *modulo.split(".")] if modulo else [PAQUETE]
    if not es_paquete:
        paquete = paquete[:-1]
    nombres: set[str] = set()
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level:
                base = paquete[: len(paquete) - (nodo.level - 1)]
                origen = ".".join([*base, *([nodo.module] if nodo.module else [])])
            else:
                origen = nodo.module or ""
            nombres.add(origen)
            nombres.update(f"{origen}.{alias.name}" for alias in nodo.names)
    return nombres


def _dentro(nombre: str, raiz: str) -> bool:
    return nombre == raiz or nombre.startswith(raiz + ".")


def _internos(nombres: set[str]) -> set[str]:
    return {
        nombre.removeprefix(PAQUETE + ".") for nombre in nombres if nombre.startswith(PAQUETE + ".")
    }


def test_la_tabla_de_fronteras_nombra_modulos_que_existen() -> None:
    modulos = _modulos()
    for nombre in {*PROHIBIDOS, *SOLO_INTERNOS, *SOLO_EXTERNOS, *SIN_OPEN, UNICO_CON_RED}:
        assert nombre in modulos, f"el módulo {nombre} no existe"


def test_fronteras_de_import() -> None:
    modulos = _modulos()
    faltas: list[str] = []
    for modulo, ruta in modulos.items():
        nombres = _imports(modulo, ruta)
        internos = _internos(nombres)

        prohibidos_internos, prohibidos_externos = PROHIBIDOS.get(modulo, ((), ()))
        for prohibido in prohibidos_internos:
            if any(_dentro(nombre, prohibido) for nombre in internos):
                faltas.append(f"{modulo} importa la capa {prohibido}")
        for prohibido in prohibidos_externos:
            if any(_dentro(nombre, prohibido) for nombre in nombres):
                faltas.append(f"{modulo} importa {prohibido}")

        if modulo in SOLO_INTERNOS:
            permitidos = SOLO_INTERNOS[modulo]
            for nombre in internos:
                # Un paquete ancestro de un módulo permitido también vale.
                if not any(_dentro(nombre, p) or _dentro(p, nombre) for p in permitidos):
                    faltas.append(f"{modulo} importa {nombre}")

        if modulo in SOLO_EXTERNOS:
            for nombre in nombres - {n for n in nombres if n.startswith(PAQUETE)}:
                if not any(_dentro(nombre, p) for p in SOLO_EXTERNOS[modulo]):
                    faltas.append(f"{modulo} importa {nombre}")

        if modulo in SIN_OPEN:
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            if any(isinstance(n, ast.Name) and n.id == "open" for n in ast.walk(arbol)):
                faltas.append(f"{modulo} usa open()")
    assert not faltas, "\n".join(faltas)


def test_solo_el_extractor_opcional_importa_red() -> None:
    faltas = [
        f"{modulo} importa {red}"
        for modulo, ruta in _modulos().items()
        if modulo != UNICO_CON_RED
        for red in RED
        if any(_dentro(nombre, red) for nombre in _imports(modulo, ruta))
    ]
    assert not faltas, "\n".join(faltas)


def test_el_detector_de_imports_resuelve_relativos(tmp_path: Path) -> None:
    ruta = tmp_path / "x.py"
    ruta.write_text(
        "from .. import cuarentena\nfrom ..db.conexion import abrir\nimport os.path\n"
        "from . import rutas\n",
        encoding="utf-8",
    )
    assert _imports("landing.recepcion", ruta) >= {
        "carga_operativa.cuarentena",
        "carga_operativa.db.conexion",
        "carga_operativa.db.conexion.abrir",
        "carga_operativa.landing.rutas",
        "os.path",
    }
