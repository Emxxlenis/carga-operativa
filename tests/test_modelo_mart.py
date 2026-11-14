"""Modelo y mart: un grano declarado, y nada del formato de origen."""

from __future__ import annotations

from carga_operativa import pipeline
from carga_operativa.config import Rutas
from carga_operativa.extraccion.determinista import ExtractorDeterminista
from carga_operativa.mart import construir as mart
from carga_operativa.modelo import construir as modelo
from dobles import DIA, Buzon, consultar, fila, registro, reloj_fijo

TABLAS_MART = ("mart_produccion", "mart_mortalidad", "mart_ventas", "mart_resumen_dia")
PALABRAS_DE_FORMATO = (
    "archivo",
    "hoja",
    "fila",
    "formato",
    "xlsx",
    "excel",
    "xml",
    "eml",
    "correo",
    "sha",
    "ruta",
    "extractor",
    "unidad",
    "nombre",
)


def _cargar_tres_fuentes(buzon: Buzon) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", valor=100.5), fila("L-002", valor=90.0)])
    buzon.xml("mortalidad.xml", [registro("L-001", valor="3"), registro("L-003", valor="1")])
    buzon.eml("ventas.eml", ["ventas lote L-001: 80.5 kg"])


def test_modelo_una_fila_por_dia_y_lote(rutas: Rutas, buzon: Buzon, correr) -> None:
    _cargar_tres_fuentes(buzon)
    assert correr().cuadra

    # Tres archivos de tres formatos hablan de L-001: queda una sola fila.
    assert consultar(
        rutas,
        """
        SELECT h.fecha, d.codigo, h.produccion_kg, h.mortalidad_aves, h.ventas_kg
        FROM hecho_dia_lote h JOIN dim_lote d USING (lote_id)
        ORDER BY d.codigo
        """,
    ) == [
        (DIA, "L-001", 100.5, 3, 80.5),
        (DIA, "L-002", 90.0, None, None),  # no reportado: NULL, no cero
        (DIA, "L-003", None, 1, None),
    ]
    assert (
        consultar(
            rutas,
            "SELECT fecha, lote_id, COUNT(*) FROM hecho_dia_lote GROUP BY 1, 2 HAVING COUNT(*) > 1",
        )
        == []
    )
    assert consultar(rutas, "SELECT * FROM dim_fecha") == [(DIA, 2026, 11, 12, 4, 46)]
    assert consultar(rutas, "SELECT COUNT(*) FROM dim_lote") == [(6,)]

    # Reconstruir no duplica ni cambia nada.
    antes = consultar(rutas, "SELECT * FROM hecho_dia_lote ORDER BY 1, 2")
    modelo.construir(rutas.base, DIA)
    mart.construir(rutas.base, DIA)
    assert consultar(rutas, "SELECT * FROM hecho_dia_lote ORDER BY 1, 2") == antes


def test_mart_refleja_el_modelo_y_no_inventa_ceros(rutas: Rutas, buzon: Buzon, correr) -> None:
    _cargar_tres_fuentes(buzon)
    correr()

    assert consultar(rutas, "SELECT * FROM mart_produccion ORDER BY lote") == [
        (DIA, "L-001", "Galpón A", 100.5),
        (DIA, "L-002", "Galpón A", 90.0),
    ]
    assert consultar(rutas, "SELECT lote, mortalidad_aves FROM mart_mortalidad ORDER BY lote") == [
        ("L-001", 3),
        ("L-003", 1),
    ]
    assert consultar(rutas, "SELECT lote, ventas_kg FROM mart_ventas") == [("L-001", 80.5)]
    assert consultar(rutas, "SELECT * FROM mart_resumen_dia") == [(DIA, 190.5, 4, 80.5, 2, 2, 1)]


def test_mart_sin_columnas_de_formato(base: Rutas) -> None:
    for tabla in TABLAS_MART:
        columnas = [fila[1] for fila in consultar(base, f"PRAGMA table_info({tabla})")]
        assert columnas, tabla
        for columna in columnas:
            for palabra in PALABRAS_DE_FORMATO:
                assert palabra not in columna.lower(), (
                    f"{tabla}.{columna} delata el formato de origen ({palabra})"
                )
    assert [fila[1] for fila in consultar(base, "PRAGMA table_info(mart_resumen_dia)")] == [
        "fecha",
        "produccion_kg",
        "mortalidad_aves",
        "ventas_kg",
        "lotes_con_produccion",
        "lotes_con_mortalidad",
        "lotes_con_ventas",
    ]


def test_el_mart_no_cambia_segun_el_formato_de_origen(rutas: Rutas, buzon: Buzon, correr) -> None:
    """El mismo dato, llegue por Excel, XML o correo, deja el mismo mart."""
    buzon.xlsx("a.xlsx", [fila("L-001", "ventas", 80.5, "kg")])
    correr()
    por_excel = [consultar(rutas, f"SELECT * FROM {t}") for t in TABLAS_MART]

    for nombre, escribir in (
        ("xml", lambda b: b.xml("a.xml", [registro("L-001", "ventas", "80.5", "kg")])),
        ("eml", lambda b: b.eml("a.eml", ["ventas lote L-001: 80.5 kg"])),
    ):
        otras = Rutas(raiz=rutas.raiz.parent / f"datos_{nombre}")
        escribir(Buzon(otras))
        assert pipeline.dia(otras, DIA, extractor=ExtractorDeterminista(), reloj=reloj_fijo).cuadra
        assert [consultar(otras, f"SELECT * FROM {t}") for t in TABLAS_MART] == por_excel, nombre
