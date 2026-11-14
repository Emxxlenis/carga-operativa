"""Cuarentena: motivos cerrados, detalle sin trazas y nada se borra en silencio."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from carga_operativa import cuarentena
from carga_operativa.config import Rutas
from carga_operativa.contrato import motivos
from carga_operativa.contrato.motivos import MOTIVOS_ARCHIVO, MOTIVOS_FILA, Motivo
from carga_operativa.db.conexion import AccesoCapaProhibido, abrir
from dobles import AHORA, DIA, Buzon, ExtractorGuionado, consultar, fila, motivos_del_dia

ESQUEMA = Path(__file__).resolve().parents[1] / "src" / "carga_operativa" / "db" / "esquema.sql"


def _motivos_del_check() -> set[str]:
    sql = ESQUEMA.read_text(encoding="utf-8")
    tabla = sql[sql.index("CREATE TABLE IF NOT EXISTS cuarentena") :]
    lista = re.search(r"motivo\s+TEXT NOT NULL CHECK \(motivo IN \((.*?)\)\)", tabla, re.DOTALL)
    assert lista, "no se encontró el CHECK de motivo"
    return set(re.findall(r"'([a-z_]+)'", lista.group(1)))


def test_motivos_son_lista_cerrada(base: Rutas) -> None:
    del_codigo = {motivo.value for motivo in Motivo}
    assert del_codigo == _motivos_del_check(), "el código y el CHECK del esquema difieren"
    assert set(MOTIVOS_FILA) | set(MOTIVOS_ARCHIVO) == set(Motivo)
    assert not set(MOTIVOS_FILA) & set(MOTIVOS_ARCHIVO)
    assert (len(MOTIVOS_FILA), len(MOTIVOS_ARCHIVO)) == (11, 9)

    # Un motivo de fuera de la lista no entra: ni por la función ni por SQL directo.
    con = abrir(base.base, "contrato")
    try:
        with pytest.raises(ValueError, match="lista cerrada"):
            cuarentena.registrar(
                con,
                dia_carga=DIA,
                nombre_archivo="a.xml",
                archivo_id=None,
                n_fila=1,
                motivo="otro",
                campos={},
                fila_cruda={},
                ahora=AHORA,
            )
    finally:
        con.close()
    cruda = sqlite3.connect(base.base)
    cruda.create_function("capa_actual", 0, lambda: "contrato")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            cruda.execute(
                "INSERT INTO cuarentena "
                "(dia_carga, nombre_archivo, nivel, n_fila, motivo, detalle, creado_en) "
                "VALUES (?, 'a.xml', 'fila', 1, 'otro', 'x', ?)",
                (DIA, AHORA),
            )
    finally:
        cruda.close()
    assert consultar(base, "SELECT COUNT(*) FROM cuarentena") == [(0,)]


@pytest.mark.parametrize("motivo", list(Motivo))
def test_cada_motivo_tiene_plantilla_y_explicacion(motivo: Motivo) -> None:
    detalle = motivos.redactar(motivo)
    assert detalle.endswith(".")
    assert "{" not in detalle and "}" not in detalle
    assert motivos.explicar(motivo)
    assert motivos.es_de_archivo(motivo) == (motivo in MOTIVOS_ARCHIVO)


PROHIBIDO_EN_DETALLE = (
    "Traceback",
    'File "',
    "openpyxl",
    "zipfile",
    "defusedxml",
    "BadZipFile",
    "Error",
    "Exception",
    ".py",
    "/",
    "\\",
)


def test_detalle_de_cuarentena_sin_traza(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.crudo("a_corrupto.xlsx", b"PK\x03\x04 esto no es un libro")
    buzon.crudo("b_mal_formado.xml", b"<carga><registro></carga>")
    buzon.crudo("c_binario.eml", bytes(range(256)) * 4)
    buzon.crudo("d_vacio.xml", b"<carga></carga>")
    buzon.eml("e_extractor.eml", ["ventas lote L-001: 10 kg"])

    class ExtractorQueDevuelveUnError:
        nombre = "api"

        def extraer(self, _correo: object) -> object:
            return 'Traceback (most recent call last):\n  File "/usr/lib/x.py", line 1'

    resultado = correr(extractor=ExtractorQueDevuelveUnError())

    rechazos = consultar(
        rutas, "SELECT nombre_archivo, motivo, detalle, fila_cruda_json FROM cuarentena ORDER BY 1"
    )
    assert [(nombre, motivo) for nombre, motivo, _d, _f in rechazos] == [
        ("a_corrupto.xlsx", "archivo_ilegible"),
        ("b_mal_formado.xml", "archivo_ilegible"),
        ("c_binario.eml", "extraccion_invalida"),
        ("d_vacio.xml", "archivo_ilegible"),
        ("e_extractor.eml", "extraccion_invalida"),
    ]
    for nombre, _motivo, detalle, fila_cruda in rechazos:
        for prohibido in PROHIBIDO_EN_DETALLE:
            assert prohibido not in detalle, f"{nombre}: el detalle contiene {prohibido!r}"
        assert str(rutas.raiz) not in detalle
        assert fila_cruda is None
    assert resultado.cuadra


def test_detalle_trunca_y_quita_caracteres_de_control(rutas: Rutas, buzon: Buzon, correr) -> None:
    largo = "L-" + "9" * 500
    buzon.eml("ventas.eml", ["ventas lote L-001: 10 kg"])
    extractor = ExtractorGuionado(
        [
            {"fecha": DIA, "lote": largo, "metrica": "ventas", "valor": 1, "unidad": "kg"},
            {
                "fecha": DIA,
                "lote": "L-001\x1b[31m\n\x00rojo",
                "metrica": "ventas",
                "valor": 1,
                "unidad": "kg",
            },
            {
                "fecha": DIA,
                "lote": {"a": object()},
                "metrica": "ventas",
                "valor": 1,
                "unidad": "kg",
            },
        ]
    )

    correr(extractor=extractor)

    rechazos = consultar(rutas, "SELECT motivo, detalle, fila_cruda_json FROM cuarentena")
    assert [motivo for motivo, _d, _f in rechazos] == [
        "lote_desconocido",
        "lote_desconocido",
        "tipo_invalido",
    ]
    for _motivo, detalle, fila_cruda in rechazos:
        assert len(detalle) < 200
        assert all(caracter.isprintable() for caracter in detalle)
        assert all(caracter.isprintable() for caracter in fila_cruda)
        assert isinstance(json.loads(fila_cruda), dict), "la fila cruda es JSON válido"
    assert "9" * 81 not in rechazos[0][1]
    assert "(valor de tipo dict)" in rechazos[2][1]
    assert "object at 0x" not in rechazos[2][2]


def test_cuarentena_no_se_borra_ni_edita(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", unidad="lb")])
    correr()
    antes = consultar(rutas, "SELECT * FROM cuarentena")
    assert len(antes) == 1

    cruda = sqlite3.connect(rutas.base)
    try:
        for sentencia in (
            "DELETE FROM cuarentena",
            "UPDATE cuarentena SET motivo = 'campo_extra'",
            "UPDATE cuarentena SET detalle = 'arreglado'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="solo-insercion"):
                cruda.execute(sentencia)
    finally:
        cruda.close()

    for capa in ("landing", "contrato", "conciliacion", "pronostico"):
        con = abrir(rutas.base, capa)
        try:
            with pytest.raises(AccesoCapaProhibido):
                con.execute("DELETE FROM cuarentena")
            with pytest.raises(AccesoCapaProhibido):
                con.execute("UPDATE cuarentena SET detalle = 'arreglado'")
        finally:
            con.close()

    # REPLACE tampoco pisa un rechazo ya registrado.
    con = abrir(rutas.base, "contrato")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="solo-insercion"):
            con.execute(
                "INSERT OR REPLACE INTO cuarentena "
                "(dia_carga, nombre_archivo, archivo_id, nivel, n_fila, motivo, detalle, creado_en)"
                " VALUES (?, 'produccion.xlsx', 1, 'fila', 2, 'campo_extra', 'pisado', ?)",
                (DIA, AHORA),
            )
    finally:
        con.close()

    assert consultar(rutas, "SELECT * FROM cuarentena") == antes
    assert not hasattr(cuarentena, "borrar") and not hasattr(cuarentena, "editar")
    assert motivos_del_dia(rutas) == ["unidad_distinta"]
