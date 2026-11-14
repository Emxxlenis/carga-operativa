"""Contrato: columnas, tipos, unidades y grano. No adivina ni corrige."""

from __future__ import annotations

import datetime as dt

import pytest

from carga_operativa.config import Rutas
from carga_operativa.contrato import reglas
from carga_operativa.contrato.esquema import CAMPOS
from carga_operativa.contrato.motivos import MOTIVOS_FILA, Motivo
from carga_operativa.lectores import excel
from carga_operativa.sintetico import generar
from carga_operativa.tipos import Formula
from dobles import (
    DIA,
    ENCABEZADOS,
    Buzon,
    consultar,
    fecha_mas,
    fila,
    motivos_del_dia,
    registro,
    xlsx_openpyxl,
)

LOTES = frozenset({"L-001", "L-002"})
BUENA = {"fecha": DIA, "lote": "L-001", "metrica": "produccion", "valor": 10.5, "unidad": "kg"}


def validar(campos: dict[str, object], ocupados: frozenset = frozenset()):
    return reglas.validar(campos, dia_carga=DIA, lotes=LOTES, granos_ocupados=ocupados)


def test_filas_entran_igual_aceptadas_mas_rechazadas(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx(
        "produccion.xlsx",
        [
            fila("L-001"),
            fila("L-002", unidad="lb"),
            fila("L-003", fecha="2026-02-30"),
            fila("L-004"),
            fila("L-004"),  # mismo grano dentro del archivo
        ],
    )
    buzon.xml(
        "mortalidad.xml",
        [registro("L-001"), registro("L-999"), registro("L-002", valor="2.5")],
    )
    buzon.eml("ventas.eml", ["ventas lote L-001: 80 kg", "ventas lote L-002: 70"])
    buzon.crudo("roto.xlsx", b"no soy un libro")

    resultado = correr()

    por_archivo = consultar(
        rutas,
        """
        SELECT l.nombre_original, c.estado, c.filas_leidas,
          (SELECT COUNT(*) FROM contrato_fila_aceptada a WHERE a.archivo_id = c.archivo_id),
          (SELECT COUNT(*) FROM cuarentena q
            WHERE q.archivo_id = c.archivo_id AND q.nivel = 'fila')
        FROM contrato_archivo c JOIN landing_archivo l USING (archivo_id)
        ORDER BY l.nombre_original
        """,
    )
    assert por_archivo == [
        ("mortalidad.xml", "procesado", 3, 1, 2),
        ("produccion.xlsx", "procesado", 5, 2, 3),
        ("roto.xlsx", "rechazado", 0, 0, 0),
        ("ventas.eml", "procesado", 2, 1, 1),
    ]
    for nombre, _estado, leidas, aceptadas, rechazadas in por_archivo:
        assert leidas == aceptadas + rechazadas, (
            f"{nombre}: leídas {leidas}, aceptadas {aceptadas}, rechazadas {rechazadas}"
        )
    # El resumen que devuelve el pipeline dice lo mismo que la base.
    assert sorted(
        (a.nombre, a.filas_leidas, a.aceptadas, a.rechazadas) for a in resultado.archivos
    ) == [(nombre, leidas, ac, re) for nombre, _e, leidas, ac, re in por_archivo]
    assert resultado.cuadra


@pytest.mark.parametrize(
    ("fecha", "motivo"),
    [
        ("2026-02-30", "fecha_invalida"),
        ("2026-11-31", "fecha_invalida"),
        ("31/12/2026", "fecha_invalida"),
        ("12/11/2026", "fecha_invalida"),
        ("2026-11-12T00:00:00", "fecha_invalida"),
        ("20261112", "fecha_invalida"),
        ("", "columna_faltante"),
        (fecha_mas(1), "fecha_fuera_de_dia"),
        (fecha_mas(365), "fecha_fuera_de_dia"),
        (fecha_mas(-1), "fecha_fuera_de_dia"),
    ],
)
def test_fecha_imposible_va_a_cuarentena_con_motivo(
    rutas: Rutas, buzon: Buzon, correr, fecha: str, motivo: str
) -> None:
    buzon.xml("mortalidad.xml", [registro("L-001", fecha=fecha), registro("L-002")])

    resultado = correr()

    rechazos = consultar(rutas, "SELECT n_fila, motivo, detalle FROM cuarentena")
    assert [(n, m) for n, m, _detalle in rechazos] == [(1, motivo)]
    if motivo != "columna_faltante":
        assert fecha in rechazos[0][2], "el detalle nombra la fecha recibida"
    # La fila mala no llegó a ninguna capa; la buena sí.
    assert consultar(rutas, "SELECT lote FROM contrato_fila_aceptada") == [("L-002",)]
    assert consultar(rutas, "SELECT fecha, mortalidad_aves FROM mart_resumen_dia") == [(DIA, 3)]
    assert resultado.cuadra


@pytest.mark.parametrize("unidad", ["lb", "t", "docenas", "KG.", "g"])
def test_unidad_distinta_va_a_cuarentena_con_motivo(
    rutas: Rutas, buzon: Buzon, correr, unidad: str
) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", valor=220.5, unidad=unidad), fila("L-002")])

    resultado = correr()

    rechazos = consultar(rutas, "SELECT motivo, detalle FROM cuarentena")
    assert [motivo for motivo, _ in rechazos] == ["unidad_distinta"]
    detalle = rechazos[0][1]
    assert f"recibida es {unidad} " in detalle
    assert "espera kg " in detalle
    # No hubo conversión: el 220.5 no llegó al hecho con ninguna unidad.
    assert consultar(rutas, "SELECT produccion_kg FROM hecho_dia_lote") == [(100.5,)]
    assert resultado.cuadra


def test_la_unidad_se_compara_sin_espacios_ni_mayusculas(
    rutas: Rutas, buzon: Buzon, correr
) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", unidad=" KG ")])

    correr()

    assert motivos_del_dia(rutas) == []
    assert consultar(rutas, "SELECT unidad FROM contrato_fila_aceptada") == [("kg",)]


def test_columna_faltante_no_se_adivina(rutas: Rutas, buzon: Buzon, correr) -> None:
    sin_unidad = [c for c in ENCABEZADOS if c != "unidad"]
    buzon.crudo(
        "produccion.xlsx",
        generar.xlsx_bytes(
            [
                sin_unidad,
                [DIA, "L-001", "produccion", 100.5],
                [DIA, "L-002", "produccion", 90.0],
            ]
        ),
    )

    resultado = correr()

    assert consultar(rutas, "SELECT COUNT(*) FROM contrato_fila_aceptada") == [(0,)]
    rechazos = consultar(rutas, "SELECT motivo, detalle FROM cuarentena ORDER BY n_fila")
    assert [motivo for motivo, _ in rechazos] == ["columna_faltante", "columna_faltante"]
    assert "unidad" in rechazos[0][1]
    assert consultar(rutas, "SELECT COUNT(*) FROM mart_produccion") == [(0,)]
    assert resultado.cuadra


def test_encabezado_parecido_no_se_toma_por_bueno(rutas: Rutas, buzon: Buzon, correr) -> None:
    parecidos = ["Fecha", "lote", "metrica", "valor", "unidades"]
    buzon.crudo("produccion.xlsx", generar.xlsx_bytes([parecidos, fila()]))

    correr()

    assert motivos_del_dia(rutas) == ["campo_extra"]
    assert consultar(rutas, "SELECT COUNT(*) FROM contrato_fila_aceptada") == [(0,)]


def test_fila_vacia_de_excel_no_cuenta(rutas: Rutas, buzon: Buzon, correr) -> None:
    vacia = [None, None, None, None, None]
    buzon.crudo(
        "produccion.xlsx",
        xlsx_openpyxl(
            [ENCABEZADOS, fila("L-001"), vacia, ["", "  ", None, None, None], fila("L-002"), vacia]
        ),
    )

    resultado = correr()

    assert consultar(rutas, "SELECT filas_leidas FROM contrato_archivo") == [(2,)]
    assert consultar(rutas, "SELECT n_fila, lote FROM contrato_fila_aceptada ORDER BY 1") == [
        (2, "L-001"),
        (5, "L-002"),
    ]
    assert motivos_del_dia(rutas) == []
    assert resultado.cuadra


def test_celda_con_tipo_fecha_se_pasa_a_iso_y_dato_sin_encabezado_no_se_pierde() -> None:
    filas = excel.leer(
        xlsx_openpyxl(
            [
                ENCABEZADOS,
                fila("L-001", fecha=dt.date(2026, 11, 12)),
                [*fila("L-002"), None, "comentario suelto"],
            ]
        )
    )
    assert validar(filas[0].campos) == reglas.Aceptada(DIA, "L-001", "produccion", 100.5, "kg")
    assert set(filas[1].campos) == {*CAMPOS, "columna_7"}
    assert validar(filas[1].campos).motivo is Motivo.CAMPO_EXTRA


def test_correo_pasa_por_el_mismo_contrato(
    rutas: Rutas, buzon: Buzon, correr, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistos: list[dict[str, object]] = []
    original = reglas.validar

    def espia(campos, **kwargs):
        vistos.append(dict(campos))
        return original(campos, **kwargs)

    monkeypatch.setattr(reglas, "validar", espia)
    buzon.eml(
        "ventas.eml",
        ["ventas lote L-001: 80.5 kg", "ventas lote L-002: 70 lb", "ventas lote L-003: mucho kg"],
    )

    resultado = correr()

    # Cada registro del correo pasó por la misma función que Excel y XML.
    assert vistos == [
        {"fecha": DIA, "metrica": "ventas", "lote": "L-001", "valor": "80.5", "unidad": "kg"},
        {"fecha": DIA, "metrica": "ventas", "lote": "L-002", "valor": "70", "unidad": "lb"},
        {"fecha": DIA, "metrica": "ventas", "lote": "L-003", "valor": "mucho", "unidad": "kg"},
    ]
    assert consultar(rutas, "SELECT ventas_kg FROM hecho_dia_lote") == [(80.5,)]
    assert motivos_del_dia(rutas) == ["unidad_distinta", "tipo_invalido"]
    assert consultar(rutas, "SELECT extractor FROM contrato_archivo") == [("determinista",)]
    assert resultado.cuadra


# --- Reglas puras: un solo motivo, el primero que falla -------------------------

CASOS = [
    ({**BUENA, "confianza": 0.9}, Motivo.CAMPO_EXTRA),
    ({k: v for k, v in BUENA.items() if k != "unidad"}, Motivo.COLUMNA_FALTANTE),
    ({**BUENA, "lote": "   "}, Motivo.COLUMNA_FALTANTE),
    ({**BUENA, "valor": None}, Motivo.COLUMNA_FALTANTE),
    ({**BUENA, "valor": Formula("=1+1")}, Motivo.FORMULA_NO_EVALUADA),
    ({**BUENA, "fecha": Formula("=HOY()")}, Motivo.FORMULA_NO_EVALUADA),
    ({**BUENA, "valor": "mucho"}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": "1,5"}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": "1.234,5"}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": "1e3"}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": " 10"}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": True}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": float("nan")}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": float("inf")}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "valor": [10]}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "metrica": "mortalidad", "unidad": "aves", "valor": 2.5}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "lote": 1}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "fecha": 20261112}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "unidad": 5}, Motivo.TIPO_INVALIDO),
    ({**BUENA, "fecha": "2026-02-30"}, Motivo.FECHA_INVALIDA),
    ({**BUENA, "fecha": dt.datetime(2026, 11, 12, 8, 30)}, Motivo.FECHA_INVALIDA),
    ({**BUENA, "fecha": "2026-11-13"}, Motivo.FECHA_FUERA_DE_DIA),
    ({**BUENA, "metrica": "huevos"}, Motivo.METRICA_DESCONOCIDA),
    ({**BUENA, "metrica": "Produccion"}, Motivo.METRICA_DESCONOCIDA),
    ({**BUENA, "unidad": "lb"}, Motivo.UNIDAD_DISTINTA),
    ({**BUENA, "metrica": "mortalidad", "valor": 3}, Motivo.UNIDAD_DISTINTA),
    ({**BUENA, "lote": "L-999"}, Motivo.LOTE_DESCONOCIDO),
    ({**BUENA, "lote": "l-001"}, Motivo.LOTE_DESCONOCIDO),
    ({**BUENA, "valor": -1}, Motivo.VALOR_FUERA_DE_RANGO),
    ({**BUENA, "valor": 50_000.001}, Motivo.VALOR_FUERA_DE_RANGO),
    ({**BUENA, "valor": 10**400}, Motivo.VALOR_FUERA_DE_RANGO),
]


@pytest.mark.parametrize(("campos", "motivo"), CASOS)
def test_cada_regla_rechaza_con_su_motivo(campos: dict[str, object], motivo: Motivo) -> None:
    resultado = validar(campos)
    assert isinstance(resultado, reglas.Rechazada)
    assert resultado.motivo is motivo


def test_los_casos_cubren_todos_los_motivos_de_fila() -> None:
    cubiertos = {motivo for _campos, motivo in CASOS} | {Motivo.GRANO_DUPLICADO}
    assert cubiertos == set(MOTIVOS_FILA)


def test_un_solo_motivo_el_primero_que_falla() -> None:
    """Una fila con todo mal informa solo el primer motivo del orden fijo."""
    todo_mal = {
        "fecha": "2026-02-30",
        "lote": "L-999",
        "metrica": "huevos",
        "valor": -5,
        "unidad": "lb",
        "confianza": 1,
    }
    esperados = [
        Motivo.CAMPO_EXTRA,
        Motivo.FECHA_INVALIDA,
        Motivo.METRICA_DESCONOCIDA,
        Motivo.UNIDAD_DISTINTA,
        Motivo.LOTE_DESCONOCIDO,
        Motivo.VALOR_FUERA_DE_RANGO,
    ]
    arreglos = [
        ("confianza", None),
        ("fecha", DIA),
        ("metrica", "produccion"),
        ("unidad", "kg"),
        ("lote", "L-001"),
        ("valor", 5),
    ]
    campos = dict(todo_mal)
    for esperado, (clave, valor) in zip(esperados, arreglos, strict=True):
        assert validar(campos).motivo is esperado
        if valor is None:
            del campos[clave]
        else:
            campos[clave] = valor
    assert isinstance(validar(campos), reglas.Aceptada)
    assert validar(campos, frozenset({(DIA, "L-001", "produccion")})).motivo is (
        Motivo.GRANO_DUPLICADO
    )


def test_fila_aceptada_tiene_exactamente_los_cinco_campos() -> None:
    aceptada = validar({**BUENA, "valor": "10.250", "unidad": " KG "})
    assert aceptada == reglas.Aceptada(
        fecha=DIA, lote="L-001", metrica="produccion", valor=10.25, unidad="kg"
    )
    assert tuple(vars(aceptada)) == CAMPOS
