"""Reejecutar un día no duplica, y un dato aceptado no se pisa."""

from __future__ import annotations

from carga_operativa.config import Rutas
from dobles import Buzon, consultar, fila, motivos_del_dia, registro

TABLAS = (
    "landing_archivo",
    "ref_lote",
    "contrato_archivo",
    "contrato_fila_aceptada",
    "cuarentena",
    "dim_fecha",
    "dim_lote",
    "hecho_dia_lote",
    "mart_produccion",
    "mart_mortalidad",
    "mart_ventas",
    "mart_resumen_dia",
    "conciliacion_dia",
    "conciliacion_control",
    "pronostico_linea_base",
)


def _contenido(rutas: Rutas) -> dict[str, list[tuple]]:
    return {tabla: consultar(rutas, f"SELECT * FROM {tabla} ORDER BY 1, 2") for tabla in TABLAS}


def _en_disco(rutas: Rutas) -> list[str]:
    return sorted(str(p.relative_to(rutas.landing)) for p in rutas.landing.rglob("*"))


def test_reejecutar_mismo_dia_no_duplica(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx(
        "produccion.xlsx", [fila("L-001"), fila("L-002", unidad="lb"), fila("L-003", valor=90.0)]
    )
    buzon.xml("mortalidad.xml", [registro("L-001"), registro("L-999")])
    buzon.eml("ventas.eml", ["ventas lote L-001: 80 kg", "ventas lote L-002: 70"])
    buzon.crudo("macros.xlsm", b"lo que sea")
    (buzon.carpeta() / "enlace.xlsx").symlink_to(buzon.carpeta() / "produccion.xlsx")

    primera = correr()
    antes = _contenido(rutas)
    disco_antes = _en_disco(rutas)

    segunda = correr()
    tercera = correr()

    assert _contenido(rutas) == antes
    assert _en_disco(rutas) == disco_antes
    assert (primera.recepcion.nuevos, primera.recepcion.ya_recibidos) == (4, 0)
    for repetida in (segunda, tercera):
        assert (repetida.recepcion.nuevos, repetida.recepcion.ya_recibidos) == (0, 4)
        assert repetida.archivos == (), "no queda nada pendiente por procesar"
        assert repetida.cuadra
        assert repetida.controles == primera.controles
    assert len(antes["landing_archivo"]) == 4
    assert len(antes["cuarentena"]) == 5  # 3 filas, 1 archivo con macros, 1 enlace


def test_mismo_contenido_con_otro_nombre_no_reingresa(rutas: Rutas, buzon: Buzon, correr) -> None:
    original = buzon.xlsx("produccion.xlsx", [fila("L-001"), fila("L-002", valor=90.0)])
    correr()
    antes = _contenido(rutas)

    buzon.crudo("produccion_copia_final.xlsx", original.read_bytes())
    resultado = correr()

    assert (resultado.recepcion.nuevos, resultado.recepcion.ya_recibidos) == (0, 2)
    assert _contenido(rutas) == antes
    assert consultar(rutas, "SELECT nombre_original FROM landing_archivo") == [("produccion.xlsx",)]
    assert consultar(rutas, "SELECT produccion_kg FROM mart_resumen_dia") == [(190.5,)]
    assert resultado.cuadra

    # Tampoco reingresa si el mismo contenido vuelve otro día.
    buzon.crudo("produccion.xlsx", original.read_bytes(), dia="2026-11-13")
    otro_dia = correr("2026-11-13")
    assert (otro_dia.recepcion.nuevos, otro_dia.recepcion.ya_recibidos) == (0, 1)
    assert consultar(rutas, "SELECT COUNT(*) FROM landing_archivo") == [(1,)]
    assert consultar(rutas, "SELECT COUNT(*) FROM mart_resumen_dia") == [(1,)]


def test_archivo_corregido_no_pisa_grano_aceptado(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", valor=100.5), fila("L-002", valor=90.0)])
    correr()

    # Llega una "corrección" para L-001 y un dato nuevo para L-003.
    buzon.xlsx("produccion_corregido.xlsx", [fila("L-001", valor=999.0), fila("L-003", valor=70.0)])
    resultado = correr()

    assert consultar(rutas, "SELECT lote, valor FROM contrato_fila_aceptada ORDER BY lote") == [
        ("L-001", 100.5),
        ("L-002", 90.0),
        ("L-003", 70.0),
    ]
    assert consultar(rutas, "SELECT lote, produccion_kg FROM mart_produccion ORDER BY lote") == [
        ("L-001", 100.5),
        ("L-002", 90.0),
        ("L-003", 70.0),
    ]
    rechazos = consultar(rutas, "SELECT nombre_archivo, motivo, detalle FROM cuarentena")
    assert [(nombre, motivo) for nombre, motivo, _ in rechazos] == [
        ("produccion_corregido.xlsx", "grano_duplicado")
    ]
    assert "L-001" in rechazos[0][2] and "no se reemplaza" in rechazos[0][2]
    assert consultar(rutas, "SELECT produccion_kg FROM mart_resumen_dia") == [(260.5,)]
    assert resultado.cuadra, "el rechazo está contado: el día cuadra igual"
    assert motivos_del_dia(rutas) == ["grano_duplicado"]
