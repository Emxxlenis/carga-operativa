"""Datos sintéticos de demostración, con semilla fija.

Nada de esto es real: lotes, valores, nombres de archivo y el correo se
inventan aquí. Mismo catálogo y misma semilla producen los mismos bytes. No se
usa el reloj ni el generador global de ``random``.

El día de la demo siembra defectos a propósito: un libro con macros, una celda
con fórmula, una fila en ``lb``, una fecha imposible, un lote inexistente y una
línea de correo sin unidad.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections.abc import Sequence
from email.utils import format_datetime
from pathlib import Path
from random import Random
from xml.sax.saxutils import escape, quoteattr

from ..config import DIA_DEMO, DIAS_HISTORIA, SEMILLA
from ..tipos import Formula

ENCABEZADOS = ("fecha", "lote", "metrica", "valor", "unidad")
LOTE_INEXISTENTE = "L-999"
FECHA_IMPOSIBLE = "2026-11-31"
DOMINIO = "granja-demo.example"

_FECHA_ZIP = (1980, 1, 1, 0, 0, 0)
_ZONA = dt.timezone(dt.timedelta(hours=-5))
# Factor por día de la semana (lunes a domingo).
_SEMANA_PRODUCCION = (1.00, 1.02, 1.01, 0.99, 1.03, 0.95, 0.90)
_SEMANA_VENTAS = (1.05, 1.00, 0.98, 1.00, 1.08, 0.90, 0.70)

_TIPOS_CONTENIDO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)
_RELS_RAIZ = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type='
    '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/>'
    "</Relationships>"
)
_LIBRO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Carga" sheetId="1" r:id="rId1"/></sheets>'
    "</workbook>"
)
_RELS_LIBRO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type='
    '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)
_HOJA_INICIO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<sheetData>"
)
_HOJA_FIN = "</sheetData></worksheet>"


def _columna(indice: int) -> str:
    letras = ""
    numero = indice + 1
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(ord("A") + resto) + letras
    return letras


def _celda(referencia: str, valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, Formula):
        # Lleva un valor ya calculado, como lo dejaría Excel. No debe usarse.
        return f'<c r="{referencia}"><f>{escape(valor.texto.lstrip("="))}</f><v>0</v></c>'
    if isinstance(valor, bool):
        return f'<c r="{referencia}" t="b"><v>{int(valor)}</v></c>'
    if isinstance(valor, int | float):
        return f'<c r="{referencia}"><v>{valor!r}</v></c>'
    texto = escape(str(valor))
    return f'<c r="{referencia}" t="inlineStr"><is><t xml:space="preserve">{texto}</t></is></c>'


def xlsx_bytes(filas: Sequence[Sequence[object]], *, con_macros: bool = False) -> bytes:
    """Un libro xlsx mínimo de una hoja. ``filas`` incluye el encabezado."""
    hoja = [_HOJA_INICIO]
    for n_fila, fila in enumerate(filas, start=1):
        celdas = "".join(
            _celda(f"{_columna(indice)}{n_fila}", valor) for indice, valor in enumerate(fila)
        )
        hoja.append(f'<row r="{n_fila}">{celdas}</row>')
    hoja.append(_HOJA_FIN)
    partes = [
        ("[Content_Types].xml", _TIPOS_CONTENIDO.encode()),
        ("_rels/.rels", _RELS_RAIZ.encode()),
        ("xl/workbook.xml", _LIBRO.encode()),
        ("xl/_rels/workbook.xml.rels", _RELS_LIBRO.encode()),
        ("xl/worksheets/sheet1.xml", "".join(hoja).encode()),
    ]
    if con_macros:
        partes.append(("xl/vbaProject.bin", b"Demostracion: esto no es una macro real."))
    salida = io.BytesIO()
    # Sin compresión y con fecha fija: los bytes no dependen de la máquina.
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_STORED) as archivo:
        for nombre, contenido in partes:
            archivo.writestr(zipfile.ZipInfo(nombre, date_time=_FECHA_ZIP), contenido)
    return salida.getvalue()


def xml_bytes(registros: Sequence[dict[str, str]]) -> bytes:
    """``<carga>`` con un ``<registro>`` por fila: fecha y lote como atributos."""
    lineas = ['<?xml version="1.0" encoding="UTF-8"?>', "<carga>"]
    for registro in registros:
        atributos = " ".join(
            f"{clave}={quoteattr(registro[clave])}"
            for clave in ("fecha", "lote")
            if clave in registro
        )
        lineas.append(f"  <registro {atributos}>".replace(" >", ">"))
        for clave, valor in registro.items():
            if clave not in ("fecha", "lote"):
                lineas.append(f"    <{clave}>{escape(valor)}</{clave}>")
        lineas.append("  </registro>")
    lineas.append("</carga>")
    return ("\n".join(lineas) + "\n").encode("utf-8")


def eml_bytes(dia: str, lineas: Sequence[str]) -> bytes:
    """Un correo exportado con texto plano y una alternativa HTML que no se debe leer."""
    fecha = dt.date.fromisoformat(dia)
    enviado = dt.datetime(fecha.year, fecha.month, fecha.day, 18, 0, tzinfo=_ZONA)
    limite = "limite-demo-carga-operativa"
    texto = [
        "Buenas tardes,",
        "",
        "Reporte de ventas del dia. Datos de demostracion.",
        "",
        f"Fecha: {dia}",
        *lineas,
        "",
        "Saludos,",
        "Operaciones (demostracion)",
    ]
    partes = [
        f"From: Operaciones Demo <operaciones@{DOMINIO}>",
        f"To: Carga Operativa <carga@{DOMINIO}>",
        f"Subject: Ventas del dia {dia} (demostracion)",
        f"Date: {format_datetime(enviado)}",
        f"Message-ID: <ventas-{dia}@{DOMINIO}>",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/alternative; boundary="{limite}"',
        "",
        f"--{limite}",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
        "",
        *texto,
        f"--{limite}",
        'Content-Type: text/html; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
        "",
        "<html><body><p>Reporte de ventas del dia.",
        f'<a href="https://{DOMINIO}/reporte/{dia}">Ver el reporte en linea</a></p></body></html>',
        f"--{limite}--",
        "",
    ]
    return "\r\n".join(partes).encode("utf-8")


def _valores_del_dia(
    dia: str, lotes: Sequence[str], semilla: int
) -> list[tuple[str, float, int, float]]:
    """(lote, producción kg, mortalidad aves, ventas kg) para cada lote."""
    fecha = dt.date.fromisoformat(dia)
    azar = Random(f"{semilla}:{dia}")
    dia_semana = fecha.weekday()
    valores = []
    for indice, lote in enumerate(lotes):
        base = 900 + 60 * indice
        produccion = round(base * _SEMANA_PRODUCCION[dia_semana] * azar.uniform(0.97, 1.03), 1)
        mortalidad = azar.randint(0, 6)
        ventas = round(produccion * _SEMANA_VENTAS[dia_semana] * azar.uniform(0.80, 0.90), 1)
        valores.append((lote, produccion, mortalidad, ventas))
    return valores


def contenido_dia(
    dia: str, lotes: Sequence[str], *, semilla: int = SEMILLA, con_defectos: bool = False
) -> dict[str, bytes]:
    """Los archivos que "llegan" un día: nombre -> bytes."""
    valores = _valores_del_dia(dia, lotes, semilla)

    hoja: list[list[object]] = [list(ENCABEZADOS)]
    registros: list[dict[str, str]] = []
    lineas: list[str] = []
    for posicion, (lote, produccion, mortalidad, ventas) in enumerate(valores):
        fila_produccion: list[object] = [dia, lote, "produccion", produccion, "kg"]
        registro = {
            "fecha": dia,
            "lote": lote,
            "metrica": "mortalidad",
            "valor": str(mortalidad),
            "unidad": "aves",
        }
        linea = f"ventas lote {lote}: {ventas} kg"
        if con_defectos:
            if posicion == 2:
                # Celda con fórmula: no se evalúa.
                fila_produccion[3] = Formula(f"=D{posicion + 1}*1.02")
            if posicion == 4:
                # Unidad distinta: no se convierte.
                fila_produccion[3] = round(produccion * 2.20462, 1)
                fila_produccion[4] = "lb"
            if posicion == 1:
                registro["fecha"] = FECHA_IMPOSIBLE
            if posicion == 3:
                # Línea de correo sin unidad.
                linea = f"ventas lote {lote}: {ventas}"
        hoja.append(fila_produccion)
        registros.append(registro)
        lineas.append(linea)

    archivos = {}
    if con_defectos:
        registros.append(
            {
                "fecha": dia,
                "lote": LOTE_INEXISTENTE,
                "metrica": "mortalidad",
                "valor": "2",
                "unidad": "aves",
            }
        )
        anexo = [list(ENCABEZADOS), [dia, lotes[0], "produccion", 1.0, "kg"]]
        archivos[f"produccion_anexo_{dia}.xlsx"] = xlsx_bytes(anexo, con_macros=True)
    archivos[f"produccion_{dia}.xlsx"] = xlsx_bytes(hoja)
    archivos[f"mortalidad_{dia}.xml"] = xml_bytes(registros)
    archivos[f"ventas_{dia}.eml"] = eml_bytes(dia, lineas)
    return archivos


def dias_de_historia(dia_final: str = DIA_DEMO, dias: int = DIAS_HISTORIA) -> list[str]:
    """Los días generados, del más antiguo al día de la demo."""
    final = dt.date.fromisoformat(dia_final)
    return [(final - dt.timedelta(days=resta)).isoformat() for resta in range(dias - 1, -1, -1)]


def generar(
    raiz_buzon: Path,
    lotes: Sequence[str],
    *,
    semilla: int = SEMILLA,
    dia_final: str = DIA_DEMO,
    dias: int = DIAS_HISTORIA,
) -> list[str]:
    """Escribe el buzón de todos los días. Los defectos van solo en el día final."""
    generados = dias_de_historia(dia_final, dias)
    for dia in generados:
        carpeta = Path(raiz_buzon) / dia
        carpeta.mkdir(parents=True, exist_ok=True)
        archivos = contenido_dia(dia, lotes, semilla=semilla, con_defectos=dia == dia_final)
        for nombre, contenido in archivos.items():
            (carpeta / nombre).write_bytes(contenido)
    return generados
