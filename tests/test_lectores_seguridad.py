"""Entradas hostiles: XML con entidades, Excel con macros o fórmulas, correo HTML."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from carga_operativa.config import Rutas
from carga_operativa.lectores import correo, excel, xml_seguro
from carga_operativa.sintetico import generar
from carga_operativa.tipos import Formula, RechazoArchivo
from dobles import (
    DIA,
    ENCABEZADOS,
    Buzon,
    ExtractorGuionado,
    consultar,
    fila,
    motivos_del_dia,
    volcado,
    xlsx_openpyxl,
)

REGISTRO_VALIDO = (
    f'<registro fecha="{DIA}" lote="L-001"><metrica>mortalidad</metrica>'
    "<valor>3</valor><unidad>aves</unidad></registro>"
)


def _sin_filas_en_ninguna_capa(rutas: Rutas) -> None:
    for tabla in ("contrato_fila_aceptada", "hecho_dia_lote", "mart_mortalidad", "mart_produccion"):
        assert consultar(rutas, f"SELECT COUNT(*) FROM {tabla}") == [(0,)], tabla
    assert consultar(rutas, "SELECT estado, filas_leidas FROM contrato_archivo") == [
        ("rechazado", 0)
    ]


def test_xml_con_entidad_externa_se_rechaza(
    rutas: Rutas, buzon: Buzon, correr, tmp_path: Path
) -> None:
    secreto = tmp_path / "secreto.txt"
    secreto.write_text("CONTENIDO-QUE-NO-DEBE-LEERSE", encoding="utf-8")
    buzon.crudo(
        "mortalidad.xml",
        (
            '<?xml version="1.0"?>\n'
            f'<!DOCTYPE carga [<!ENTITY xxe SYSTEM "{secreto.as_uri()}">]>\n'
            f'<carga><registro fecha="{DIA}" lote="&xxe;"><metrica>mortalidad</metrica>'
            "<valor>3</valor><unidad>aves</unidad></registro></carga>"
        ).encode(),
    )

    resultado = correr()

    assert motivos_del_dia(rutas) == ["xml_con_dtd_o_entidades"]
    assert "CONTENIDO-QUE-NO-DEBE-LEERSE" not in volcado(rutas)
    _sin_filas_en_ninguna_capa(rutas)
    assert resultado.cuadra


def test_xml_bomba_de_entidades_se_rechaza(rutas: Rutas, buzon: Buzon, correr) -> None:
    niveles = "".join(f'<!ENTITY e{n} "{("&e" + str(n - 1) + ";") * 10}">' for n in range(1, 10))
    bomba = (
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE carga [<!ENTITY e0 "aaaaaaaaaa">{niveles}]>\n'
        f"<carga>{REGISTRO_VALIDO}<registro><metrica>&e9;</metrica></registro></carga>"
    )
    buzon.crudo("mortalidad.xml", bomba.encode())

    correr()

    assert motivos_del_dia(rutas) == ["xml_con_dtd_o_entidades"]
    assert "aaaaaaaaaa" not in volcado(rutas)
    _sin_filas_en_ninguna_capa(rutas)


@pytest.mark.parametrize(
    "documento",
    [
        b'<!DOCTYPE carga SYSTEM "file:///dev/null"><carga></carga>',
        b'<!DOCTYPE carga [<!ENTITY a "b">]><carga>&a;</carga>',
        b'<!DOCTYPE carga [<!ENTITY % p SYSTEM "file:///dev/null"> %p;]><carga></carga>',
    ],
)
def test_lector_xml_rechaza_dtd_y_entidades(documento: bytes) -> None:
    with pytest.raises(RechazoArchivo) as rechazo:
        xml_seguro.leer(documento)
    assert rechazo.value.motivo == "xml_con_dtd_o_entidades"


def test_xml_mal_formado_es_ilegible_y_lo_desconocido_no_se_descarta() -> None:
    with pytest.raises(RechazoArchivo) as rechazo:
        xml_seguro.leer(b"<carga><registro></carga>")
    assert (rechazo.value.motivo, rechazo.value.campos) == (
        "archivo_ilegible",
        {"causa": "xml_mal_formado"},
    )

    filas = xml_seguro.leer(
        f'<carga>{REGISTRO_VALIDO}<otro lote="L-002"><valor>1</valor><valor>2</valor>'
        "<nota><autor>x</autor></nota></otro></carga>".encode()
    )
    assert filas[0].campos == {
        "fecha": DIA,
        "lote": "L-001",
        "metrica": "mortalidad",
        "valor": "3",
        "unidad": "aves",
    }
    # Elemento inesperado, clave repetida y anidamiento quedan como campos de más.
    assert filas[1].campos == {
        "elemento": "otro",
        "lote": "L-002",
        "valor": "1",
        "valor (repetido)": "2",
        "nota": "",
        "nota.autor": "x",
    }


def test_excel_con_macros_se_rechaza(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.crudo("produccion.xlsx", generar.xlsx_bytes([ENCABEZADOS, fila()], con_macros=True))

    resultado = correr()

    assert motivos_del_dia(rutas) == ["excel_con_macros"]
    _sin_filas_en_ninguna_capa(rutas)
    assert resultado.cuadra


def test_excel_con_macros_se_detecta_aunque_el_nombre_cambie() -> None:
    limpio = generar.xlsx_bytes([ENCABEZADOS, fila()])
    salida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(limpio)) as origen, zipfile.ZipFile(salida, "w") as destino:
        for entrada in origen.infolist():
            destino.writestr(entrada, origen.read(entrada))
        destino.writestr("xl/otra/VBAPROJECT.BIN", b"x")
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(salida.getvalue())
    assert rechazo.value.motivo == "excel_con_macros"

    # Un libro declarado como habilitado para macros, aunque no traiga el binario.
    salida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(limpio)) as origen, zipfile.ZipFile(salida, "w") as destino:
        for entrada in origen.infolist():
            contenido = origen.read(entrada)
            if entrada.filename == "[Content_Types].xml":
                contenido = contenido.replace(
                    b"spreadsheetml.sheet.main+xml", b"ms-excel.sheet.macroEnabled.main+xml"
                )
            destino.writestr(entrada, contenido)
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(salida.getvalue())
    assert rechazo.value.motivo == "excel_con_macros"


@pytest.mark.parametrize("escritor", ["openpyxl", "generador"])
def test_formula_de_excel_no_se_evalua(rutas: Rutas, buzon: Buzon, correr, escritor: str) -> None:
    if escritor == "openpyxl":
        datos = xlsx_openpyxl([ENCABEZADOS, fila("L-001", valor="=1+1"), fila("L-002", valor=7)])
    else:
        # El generador deja además un valor ya calculado dentro de la celda.
        datos = generar.xlsx_bytes(
            [ENCABEZADOS, fila("L-001", valor=Formula("=1+1")), fila("L-002", valor=7)]
        )
    buzon.crudo("produccion.xlsx", datos)

    resultado = correr()

    assert consultar(rutas, "SELECT motivo, detalle FROM cuarentena") == [
        (
            "formula_no_evaluada",
            "El campo valor trae una fórmula de Excel (=1+1). No se evalúa.",
        )
    ]
    # Solo llegó la fila sin fórmula; el 2 de la fórmula no está en ninguna tabla.
    assert consultar(rutas, "SELECT lote, valor FROM contrato_fila_aceptada") == [("L-002", 7.0)]
    assert consultar(rutas, "SELECT lote, produccion_kg FROM mart_produccion") == [("L-002", 7.0)]
    assert consultar(rutas, "SELECT produccion_kg FROM mart_resumen_dia") == [(7.0,)]
    assert resultado.cuadra


def test_excel_con_dtd_interno_o_demasiado_grande_se_rechaza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limpio = generar.xlsx_bytes([ENCABEZADOS, fila()])
    salida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(limpio)) as origen, zipfile.ZipFile(salida, "w") as destino:
        for entrada in origen.infolist():
            contenido = origen.read(entrada)
            if entrada.filename == "xl/worksheets/sheet1.xml":
                contenido = contenido.replace(
                    b"<worksheet", b'<!DOCTYPE worksheet [<!ENTITY a "b">]><worksheet', 1
                )
            destino.writestr(entrada, contenido)
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(salida.getvalue())
    assert rechazo.value.campos == {"causa": "xml_interno_con_dtd"}

    monkeypatch.setattr(excel, "MAX_DESCOMPRIMIDO", 100)
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(limpio)
    assert rechazo.value.motivo == "archivo_demasiado_grande"


@pytest.mark.parametrize("datos", [b"", b"no soy un zip", b"PK\x03\x04roto"])
def test_excel_corrupto_es_ilegible(datos: bytes) -> None:
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(datos)
    assert rechazo.value.motivo == "archivo_ilegible"


def test_excel_con_varias_hojas_o_encabezado_repetido_es_ilegible() -> None:
    from openpyxl import Workbook

    libro = Workbook()
    libro.active.append(ENCABEZADOS)
    libro.create_sheet("otra").append(["dato", "suelto"])
    salida = io.BytesIO()
    libro.save(salida)
    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(salida.getvalue())
    assert rechazo.value.campos == {"causa": "varias_hojas"}

    with pytest.raises(RechazoArchivo) as rechazo:
        excel.leer(xlsx_openpyxl([[*ENCABEZADOS, "valor"], [*fila(), 1]]))
    assert rechazo.value.campos == {"causa": "encabezado_repetido"}


SOLO_HTML = (
    "From: alguien@granja-demo.example\r\n"
    "Subject: Ventas\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: text/html; charset="utf-8"\r\n'
    "\r\n"
    f"<html><body><p>Fecha: {DIA}</p><p>ventas lote L-001: 10 kg</p>"
    '<a href="https://granja-demo.example/reporte">ver</a>'
    '<img src="https://granja-demo.example/pixel.png"></body></html>\r\n'
)


def test_correo_html_y_enlaces_no_se_siguen(
    rutas: Rutas, buzon: Buzon, correr, sin_red: list[str]
) -> None:
    buzon.crudo("ventas.eml", SOLO_HTML.encode())
    extractor = ExtractorGuionado([])

    resultado = correr(extractor=extractor)

    assert motivos_del_dia(rutas) == ["correo_sin_texto_plano"]
    assert extractor.correos == [], "el HTML no llega al extractor"
    _sin_filas_en_ninguna_capa(rutas)
    assert sin_red == [], "nadie intentó abrir una conexión"
    assert resultado.cuadra


def test_correo_entrega_solo_el_texto_plano_y_no_guarda_adjuntos(
    rutas: Rutas, buzon: Buzon, correr
) -> None:
    limite = "x-limite"
    mensaje = "\r\n".join(
        [
            "From: alguien@granja-demo.example",
            "Subject: Ventas",
            "MIME-Version: 1.0",
            f'Content-Type: multipart/mixed; boundary="{limite}"',
            "",
            f"--{limite}",
            'Content-Type: text/plain; charset="utf-8"',
            "",
            f"Fecha: {DIA}",
            "ventas lote L-001: 10 kg",
            "Ver https://granja-demo.example/reporte",
            f"--{limite}",
            "Content-Type: text/plain",
            'Content-Disposition: attachment; filename="extra.txt"',
            "",
            "ventas lote L-002: 99 kg",
            f"--{limite}",
            "Content-Type: application/octet-stream",
            'Content-Disposition: attachment; filename="programa.exe"',
            "Content-Transfer-Encoding: base64",
            "",
            "TVqQAAMAAAAEAAAA",
            f"--{limite}--",
            "",
        ]
    )
    texto = correo.leer(mensaje.encode())
    assert "ventas lote L-001: 10 kg" in texto.cuerpo
    assert "L-002" not in texto.cuerpo, "un adjunto no es el cuerpo"

    buzon.crudo("ventas.eml", mensaje.encode())
    correr()

    assert consultar(rutas, "SELECT lote, ventas_kg FROM mart_ventas") == [("L-001", 10.0)]
    en_disco = sorted(p.name for p in rutas.raiz.rglob("*") if p.is_file())
    assert not any(nombre.endswith((".exe", ".txt")) for nombre in en_disco), en_disco
