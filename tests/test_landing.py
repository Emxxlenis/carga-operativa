"""Landing: lectura confinada, archivos tal como llegaron y nada editable."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from carga_operativa import __main__ as cli
from carga_operativa import pipeline
from carga_operativa.config import Rutas
from carga_operativa.contrato import aplicar
from carga_operativa.db import conexion
from carga_operativa.db.conexion import AccesoCapaProhibido, abrir
from carga_operativa.extraccion.determinista import ExtractorDeterminista
from carga_operativa.landing import recepcion
from carga_operativa.landing.rutas import RutaNoConfinada, resolver_confinada
from dobles import AHORA, DIA, Buzon, consultar, fila, motivos_del_dia


@pytest.mark.parametrize(
    "relativa",
    [
        "../x",
        "a/../../x",
        "2026-11-12/../../carga.db",
        "/etc/passwd",
        "\\\\servidor\\recurso",
        "C:\\datos",
        "..",
        "",
        ".",
        "a\x00b",
    ],
)
def test_ruta_no_escapa_de_landing(tmp_path: Path, relativa: str) -> None:
    raiz = tmp_path / "landing"
    raiz.mkdir()
    with pytest.raises(RutaNoConfinada):
        resolver_confinada(raiz, relativa)


def test_ruta_no_escapa_de_landing_por_enlace_simbolico(tmp_path: Path) -> None:
    raiz = tmp_path / "landing"
    raiz.mkdir()
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (afuera / "secreto.txt").write_text("secreto", encoding="utf-8")
    (raiz / "puente").symlink_to(afuera, target_is_directory=True)
    (raiz / "archivo").symlink_to(afuera / "secreto.txt")

    with pytest.raises(RutaNoConfinada):
        resolver_confinada(raiz, "puente/secreto.txt")
    with pytest.raises(RutaNoConfinada):
        resolver_confinada(raiz, "archivo")
    # Tampoco un enlace que apunta adentro de la raíz: no se sigue ninguno.
    (raiz / "real").mkdir()
    (raiz / "real" / "a.xml").write_text("<carga/>", encoding="utf-8")
    (raiz / "alias").symlink_to(raiz / "real", target_is_directory=True)
    (raiz / "real" / "b.xml").symlink_to(raiz / "real" / "a.xml")
    with pytest.raises(RutaNoConfinada):
        resolver_confinada(raiz, "alias/a.xml")
    with pytest.raises(RutaNoConfinada):
        resolver_confinada(raiz, "real/b.xml")
    assert resolver_confinada(raiz, "real/a.xml").read_text(encoding="utf-8") == "<carga/>"
    # Una ruta normal sí se resuelve, y queda dentro de la raíz.
    assert resolver_confinada(raiz, "2026-11-12/a.xml").parent.parent.samefile(raiz)


@pytest.mark.parametrize(
    "dia",
    ["../../etc", "2026-11-12/../..", "2026-13-01", "2026-02-30", "20261112", "2026-W46-4", ""],
)
def test_dia_invalido_no_construye_ruta(rutas: Rutas, dia: str) -> None:
    with pytest.raises(pipeline.DiaInvalido):
        pipeline.dia(rutas, dia, extractor=ExtractorDeterminista())
    assert not rutas.raiz.exists(), "un día inválido no debe tocar el disco"

    assert cli.main(["--datos", str(rutas.raiz), "dia", dia]) == cli.CODIGO_USO
    assert not rutas.raiz.exists()


def test_la_recepcion_tampoco_acepta_un_dia_que_escapa(base: Rutas) -> None:
    with pytest.raises(RutaNoConfinada):
        recepcion.recibir(base, "../../etc", AHORA)


def test_enlace_simbolico_en_buzon_va_a_cuarentena(
    rutas: Rutas, buzon: Buzon, correr, tmp_path: Path
) -> None:
    secreto = tmp_path / "secreto.xml"
    secreto.write_text("<carga><registro>contenido-ajeno</registro></carga>", encoding="utf-8")
    (buzon.carpeta() / "enlace.xml").symlink_to(secreto)
    (buzon.carpeta() / "carpeta.xlsx").mkdir()

    resultado = correr()

    assert resultado.recepcion.no_regulares == 2
    assert consultar(rutas, "SELECT COUNT(*) FROM landing_archivo") == [(0,)]
    assert consultar(
        rutas, "SELECT nombre_archivo, nivel, motivo, archivo_id FROM cuarentena ORDER BY 1"
    ) == [
        ("carpeta.xlsx", "archivo", "entrada_no_regular", None),
        ("enlace.xml", "archivo", "entrada_no_regular", None),
    ]
    # No se copió ni se siguió: en landing no hay archivos.
    assert [p for p in rutas.landing.rglob("*") if p.is_file()] == []

    correr()
    assert len(motivos_del_dia(rutas)) == 2, "reejecutar no duplica la cuarentena"


def test_la_copia_no_sigue_enlaces_ni_espera_a_una_tuberia(rutas: Rutas, buzon: Buzon) -> None:
    """Segunda barrera, por si la entrada cambia entre el listado y la apertura."""
    real = buzon.xlsx("produccion.xlsx", [fila()])
    enlace = buzon.carpeta() / "enlace.xlsx"
    enlace.symlink_to(real)
    tuberia = buzon.carpeta() / "tuberia.xml"
    os.mkfifo(tuberia)
    temporal = rutas.raiz / "temporal"

    assert recepcion._copiar_con_huella(str(enlace), temporal) is None
    assert recepcion._copiar_con_huella(str(tuberia), temporal) is None
    assert recepcion._copiar_con_huella(str(buzon.carpeta()), temporal) is None
    assert recepcion._copiar_con_huella(str(buzon.carpeta() / "no_existe"), temporal) is None
    assert not temporal.exists()
    copiado = recepcion._copiar_con_huella(str(real), temporal)
    assert copiado is not None and copiado[1] == real.stat().st_size


def test_tuberia_y_directorio_en_buzon_van_a_cuarentena(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx("produccion.xlsx", [fila()])
    os.mkfifo(buzon.carpeta() / "tuberia.xml")
    (buzon.carpeta() / "carpeta.xlsx").mkdir()
    (buzon.carpeta() / "carpeta.xlsx" / "adentro.xml").write_bytes(b"<carga/>")

    resultado = correr()

    assert consultar(
        rutas, "SELECT nombre_archivo, nivel, motivo, archivo_id FROM cuarentena ORDER BY 1"
    ) == [
        ("carpeta.xlsx", "archivo", "entrada_no_regular", None),
        ("tuberia.xml", "archivo", "entrada_no_regular", None),
    ]
    assert consultar(rutas, "SELECT nombre_original FROM landing_archivo") == [("produccion.xlsx",)]
    assert resultado.recepcion.no_regulares == 2
    assert resultado.cuadra


REEMPLAZO = (
    "INSERT OR REPLACE INTO landing_archivo "
    "(sha256, dia_carga, nombre_original, ruta_relativa, formato, bytes, recibido_en) "
    "SELECT sha256, dia_carga, 'pisado', 'otra', formato, bytes, recibido_en "
    "FROM landing_archivo"
)


def test_landing_no_se_edita(rutas: Rutas, buzon: Buzon) -> None:
    buzon.xlsx("produccion.xlsx", [fila()])
    conexion.inicializar(rutas.base)
    recepcion.recibir(rutas, DIA, AHORA)

    (relativa,) = consultar(rutas, "SELECT ruta_relativa FROM landing_archivo")[0]
    guardado = rutas.landing / relativa
    assert stat.S_IMODE(guardado.stat().st_mode) & 0o222 == 0, "el archivo no es escribible"

    # Ni por una capa ni por fuera de ellas.
    con = abrir(rutas.base, "landing")
    try:
        with pytest.raises(AccesoCapaProhibido):
            con.execute("UPDATE landing_archivo SET nombre_original = 'otro'")
        with pytest.raises(AccesoCapaProhibido):
            con.execute("DELETE FROM landing_archivo")
        # REPLACE borraría la fila anterior: la capa no puede (tendría que revisar
        # tablas hijas que no lee) y, aunque pudiera, el trigger lo aborta.
        with pytest.raises(AccesoCapaProhibido):
            con.execute(REEMPLAZO)
    finally:
        con.close()
    cruda = sqlite3.connect(rutas.base)
    try:
        cruda.execute("PRAGMA recursive_triggers = ON")
        with pytest.raises(sqlite3.IntegrityError, match="solo-insercion"):
            cruda.execute(REEMPLAZO)
        with pytest.raises(sqlite3.IntegrityError, match="solo-insercion"):
            cruda.execute("UPDATE landing_archivo SET nombre_original = 'otro'")
        with pytest.raises(sqlite3.IntegrityError, match="solo-insercion"):
            cruda.execute("DELETE FROM landing_archivo")
    finally:
        cruda.close()
    assert consultar(rutas, "SELECT nombre_original FROM landing_archivo") == [("produccion.xlsx",)]

    # Si alguien altera los bytes, el contrato no los lee.
    os.chmod(guardado, 0o644)
    with open(guardado, "ab") as archivo:
        archivo.write(b"x")
    aplicar.aplicar_dia(rutas, DIA, ExtractorDeterminista(), AHORA)

    assert motivos_del_dia(rutas) == ["archivo_alterado"]
    assert consultar(rutas, "SELECT estado, filas_leidas FROM contrato_archivo") == [
        ("rechazado", 0)
    ]
    assert consultar(rutas, "SELECT COUNT(*) FROM contrato_fila_aceptada") == [(0,)]


def test_archivo_reemplazado_por_enlace_no_se_lee(
    rutas: Rutas, buzon: Buzon, tmp_path: Path
) -> None:
    buzon.xlsx("produccion.xlsx", [fila()])
    conexion.inicializar(rutas.base)
    recepcion.recibir(rutas, DIA, AHORA)
    (relativa,) = consultar(rutas, "SELECT ruta_relativa FROM landing_archivo")[0]
    guardado = rutas.landing / relativa
    copia = tmp_path / "copia.xlsx"
    copia.write_bytes(guardado.read_bytes())
    guardado.unlink()
    guardado.symlink_to(copia)

    aplicar.aplicar_dia(rutas, DIA, ExtractorDeterminista(), AHORA)

    assert motivos_del_dia(rutas) == ["archivo_alterado"]


@pytest.mark.parametrize(
    ("nombre", "motivo"),
    [
        ("produccion.csv", "extension_no_admitida"),
        ("produccion.xls", "extension_no_admitida"),
        ("produccion.xlsb", "extension_no_admitida"),
        ("sin_extension", "extension_no_admitida"),
        ("produccion.xlsm", "excel_con_macros"),
        ("produccion.XLTM", "excel_con_macros"),
    ],
)
def test_extension_no_admitida_rechaza_el_archivo(
    rutas: Rutas, buzon: Buzon, correr, nombre: str, motivo: str
) -> None:
    buzon.crudo(nombre, b"fecha,lote,metrica,valor,unidad\n")

    resultado = correr()

    assert motivos_del_dia(rutas) == [motivo]
    assert consultar(rutas, "SELECT estado, filas_leidas FROM contrato_archivo") == [
        ("rechazado", 0)
    ]
    assert resultado.cuadra, "un archivo rechazado entero está contado y el día cuadra"


def test_archivo_demasiado_grande_no_se_abre(
    rutas: Rutas, buzon: Buzon, correr, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aplicar, "MAX_BYTES", 100)
    monkeypatch.setattr(
        recepcion, "abrir_verificado", lambda *_: pytest.fail("no debe abrirse el archivo")
    )
    buzon.xlsx("produccion.xlsx", [fila()])

    correr()

    assert motivos_del_dia(rutas) == ["archivo_demasiado_grande"]


def test_nombre_del_archivo_se_sanea_para_mostrar(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xml("mortalidad\n\tcon control.xml", [])

    correr()

    (nombre,) = consultar(rutas, "SELECT nombre_original FROM landing_archivo")[0]
    assert nombre == "mortalidad__con control.xml"
