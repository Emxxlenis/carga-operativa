"""Demo estática: sin script, sin trazas, sin emojis, y dice que es de demostración."""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from carga_operativa.config import DIA_DEMO, SEMILLA, Rutas
from carga_operativa.contrato.esquema import VERSION_CONTRATO
from carga_operativa.db.conexion import abrir
from carga_operativa.demo import exportar
from conftest import RAIZ_REPO
from dobles import DIA, Buzon, ExtractorGuionado, consultar

ARTEFACTOS = ("index.html", "estilos.css", "datos/tablero.json")


def _textos(destino: Path) -> dict[str, str]:
    return {nombre: (destino / nombre).read_text(encoding="utf-8") for nombre in ARTEFACTOS}


class _Etiquetas(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.etiquetas: list[tuple[str, dict[str, str | None]]] = []
        self.texto: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.etiquetas.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.texto.append(data)


def _analizar(html: str) -> _Etiquetas:
    analizador = _Etiquetas()
    analizador.feed(html)
    analizador.close()
    return analizador


def _es_emoji(caracter: str) -> bool:
    codigo = ord(caracter)
    return (
        codigo >= 0x1F000
        or 0x2600 <= codigo <= 0x27BF
        or 0x2B00 <= codigo <= 0x2BFF
        or 0x2300 <= codigo <= 0x23FF
        or codigo in (0x200D, 0xFE0F, 0x20E3, 0x00A9, 0x00AE, 0x2122, 0x2139, 0x3030, 0x303D)
        # "So" incluye pictogramas; se exceptúan los trazos de caja del árbol de carpetas.
        or (unicodedata.category(caracter) == "So" and not 0x2500 <= codigo <= 0x257F)
    )


def test_la_demo_se_genera_y_todos_los_dias_cuadran(demo_generada) -> None:
    rutas, destino, resultados = demo_generada
    assert len(resultados) == 35
    assert [r.dia for r in resultados][-1] == DIA_DEMO
    assert all(r.cuadra for r in resultados), "incluido el día con defectos sembrados"
    assert sorted(str(p.relative_to(destino)) for p in destino.rglob("*") if p.is_file()) == sorted(
        ARTEFACTOS
    )
    datos = json.loads((destino / "datos" / "tablero.json").read_text(encoding="utf-8"))
    assert datos["dia"] == DIA_DEMO
    assert datos["conciliacion"]["estado"] == "cuadra"
    assert len(datos["conciliacion"]["controles"]) == 6


def test_demo_sin_script_ni_recursos_externos(demo_generada) -> None:
    _rutas, destino, _ = demo_generada
    textos = _textos(destino)
    pagina = _analizar(textos["index.html"])

    nombres = {etiqueta for etiqueta, _ in pagina.etiquetas}
    assert not nombres & {
        "script",
        "iframe",
        "object",
        "embed",
        "form",
        "img",
        "video",
        "audio",
        "a",
        "base",
        "style",
        "input",
        "button",
    }, nombres
    for etiqueta, atributos in pagina.etiquetas:
        for atributo, valor in atributos.items():
            assert not atributo.lower().startswith("on"), f"<{etiqueta} {atributo}>"
            assert atributo.lower() not in ("style", "srcdoc"), f"<{etiqueta} {atributo}>"
            assert "javascript:" not in (valor or "").lower()
    enlaces = [atributos for etiqueta, atributos in pagina.etiquetas if etiqueta == "link"]
    assert enlaces == [{"rel": "stylesheet", "href": "estilos.css"}]

    politicas = [
        atributos["content"]
        for etiqueta, atributos in pagina.etiquetas
        if etiqueta == "meta" and atributos.get("http-equiv") == "Content-Security-Policy"
    ]
    assert len(politicas) == 1
    assert "default-src 'none'" in politicas[0]
    assert "unsafe" not in politicas[0] and "script-src" not in politicas[0]

    for nombre, texto in textos.items():
        minusculas = texto.lower()
        for prohibido in ("http://", "https://", "//cdn", "<script", "javascript:", "@import"):
            assert prohibido not in minusculas, f"{nombre} contiene {prohibido}"
    assert "url(" not in textos["estilos.css"].lower()
    assert "expression(" not in textos["estilos.css"].lower()


def test_demo_sin_trazas_rutas_ni_secretos(demo_generada) -> None:
    rutas, destino, _ = demo_generada
    prohibidos = (
        "Traceback",
        'File "',
        "sqlite3.",
        "Exception",
        "Error:",
        ".py",
        "carga.db",
        "carga.log",
        "/tmp",
        "/home",
        "/usr",
        "C:\\",
        str(rutas.raiz),
        str(RAIZ_REPO),
        "API_KEY",
        "Bearer",
        "Authorization",
        "password",
        "contraseña",
        "token",
        "sha256",
    )
    for nombre, texto in _textos(destino).items():
        for prohibido in prohibidos:
            assert prohibido not in texto, f"{nombre} contiene {prohibido!r}"
        assert not re.search(r"\b[0-9a-f]{40,}\b", texto), f"{nombre} trae una huella o clave"
        assert not re.search(r"\bsk-[A-Za-z0-9]{8,}", texto), nombre
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", texto), f"{nombre} trae un correo"


def test_demo_sin_emojis(demo_generada) -> None:
    _rutas, destino, _ = demo_generada
    for nombre, texto in _textos(destino).items():
        encontrados = sorted({c for c in texto if _es_emoji(c)})
        assert encontrados == [], f"{nombre}: {[hex(ord(c)) for c in encontrados]}"


def _archivos_del_repositorio() -> list[Path]:
    rutas = [
        *(RAIZ_REPO / "src").rglob("*"),
        *(RAIZ_REPO / "tests").rglob("*.py"),
        *(RAIZ_REPO / "docs").rglob("*"),
        *RAIZ_REPO.glob("*.md"),
        *RAIZ_REPO.glob("*.toml"),
        *RAIZ_REPO.glob("Makefile"),
        *RAIZ_REPO.glob("requirements.lock"),
    ]
    return [
        ruta
        for ruta in rutas
        if ruta.is_file() and "__pycache__" not in ruta.parts and ruta.suffix != ".pyc"
    ]


def test_el_repositorio_no_tiene_emojis() -> None:
    archivos = _archivos_del_repositorio()
    assert len(archivos) > 60
    for ruta in archivos:
        texto = ruta.read_text(encoding="utf-8")
        encontrados = sorted({c for c in texto if _es_emoji(c)})
        assert encontrados == [], f"{ruta.name}: {[hex(ord(c)) for c in encontrados]}"


def test_demo_declara_datos_de_demostracion(demo_generada) -> None:
    _rutas, destino, _ = demo_generada
    textos = _textos(destino)
    html = textos["index.html"]
    pagina = _analizar(html)
    visible = " ".join(" ".join(pagina.texto).split())

    assert "demostración" in re.search(r"<title>(.*?)</title>", html).group(1)
    # El aviso va arriba, antes del tablero, no escondido al pie.
    assert html.index("Datos de demostración.") < html.index("Tablero del día")
    assert "No es una granja real ni datos de un empleador." in visible
    assert f"semilla {SEMILLA}" in visible
    assert "Los defectos del día están sembrados a propósito" in visible
    assert "determinista de demostración (sin clave de API)" in visible
    assert "Línea base (promedio o estacional simple). No es un modelo predictivo validado." in (
        visible
    )
    assert '<html lang="es">' in html

    datos = json.loads(textos["datos/tablero.json"])
    assert datos["aviso"].startswith("Datos de demostración.")
    assert datos["semilla"] == SEMILLA
    assert "No es un modelo predictivo validado" in datos["linea_base"]["rotulo"]


def test_demo_muestra_el_tablero_y_lo_que_no_entro(demo_generada) -> None:
    rutas, destino, _ = demo_generada
    textos = _textos(destino)
    visible = " ".join(" ".join(_analizar(textos["index.html"]).texto).split())
    datos = json.loads(textos["datos/tablero.json"])

    # Lo que no entró, con su razón: los seis defectos sembrados.
    assert sorted(grupo["motivo"] for grupo in datos["no_entro"]) == [
        "columna_faltante",
        "excel_con_macros",
        "fecha_invalida",
        "formula_no_evaluada",
        "lote_desconocido",
        "unidad_distinta",
    ]
    for grupo in datos["no_entro"]:
        assert grupo["archivo"] in visible
        assert grupo["explicacion"] in visible
        assert grupo["detalle"] in visible
    assert f"produccion_anexo_{DIA_DEMO}.xlsx" in visible
    assert "Archivo completo" in visible

    # El tablero coincide con el mart, y lo rechazado no está sumado.
    (produccion, mortalidad, ventas) = consultar(
        rutas,
        "SELECT produccion_kg, mortalidad_aves, ventas_kg FROM mart_resumen_dia WHERE fecha = ?",
        (DIA_DEMO,),
    )[0]
    totales = {panel["metrica"]: panel["total"] for panel in datos["paneles"]}
    assert totales == {"produccion": produccion, "mortalidad": mortalidad, "ventas": ventas}
    lotes = {panel["metrica"]: [f["lote"] for f in panel["por_lote"]] for panel in datos["paneles"]}
    assert lotes["produccion"] == ["L-001", "L-002", "L-004", "L-006"]  # sin fórmula ni libras
    assert lotes["mortalidad"] == ["L-001", "L-003", "L-004", "L-005", "L-006"]
    assert lotes["ventas"] == ["L-001", "L-002", "L-003", "L-005", "L-006"]
    assert "L-999" not in json.dumps(datos["paneles"])
    assert len(datos["linea_base"]["puntos"]) == 21


HOSTIL = '"><script>alert(1)</script><img src=x onerror=alert(2)>&amp;'


def test_demo_escapa_texto_de_cuarentena(
    rutas: Rutas, buzon: Buzon, correr, tmp_path: Path
) -> None:
    buzon.eml("ventas <b>negrita<b> & 'co'.eml", ["ventas lote L-001: 80.5 kg"])
    extractor = ExtractorGuionado(
        [
            {"fecha": DIA, "lote": HOSTIL, "metrica": "ventas", "valor": 1, "unidad": "kg"},
            {"fecha": DIA, "lote": "L-001", "metrica": "ventas", "valor": 1, "unidad": HOSTIL},
            {"fecha": DIA, "lote": "L-001", "metrica": HOSTIL, "valor": 1, "unidad": "kg"},
            {"fecha": DIA, "lote": "L-001", "metrica": "ventas", "valor": 1, HOSTIL: 1},
            {"fecha": HOSTIL, "lote": "L-001", "metrica": "ventas", "valor": 1, "unidad": "kg"},
        ]
    )
    assert correr(extractor=extractor).cuadra
    assert consultar(rutas, "SELECT COUNT(*) FROM cuarentena") == [(5,)]

    destino = tmp_path / "docs"
    exportar.exportar(
        rutas.base, DIA, destino, extractor="api", version_contrato=VERSION_CONTRATO, semilla=1
    )
    html = (destino / "index.html").read_text(encoding="utf-8")
    crudo = (destino / "datos" / "tablero.json").read_text(encoding="utf-8")

    pagina = _analizar(html)
    nombres = {etiqueta for etiqueta, _ in pagina.etiquetas}
    assert not nombres & {"script", "img", "b"}, "el texto hostil no se volvió marcado"
    assert all(
        not atributo.startswith("on") for _, atributos in pagina.etiquetas for atributo in atributos
    )
    assert "<script" not in html and "onerror=alert(2)>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # El texto se ve tal cual, como texto.
    visible = "".join(pagina.texto)
    assert "<script>alert(1)</script>" in visible
    assert "&amp;amp;" in html, "un & que ya venía escapado se escapa otra vez"

    assert "<" not in crudo and ">" not in crudo and "&" not in crudo
    assert any(HOSTIL[:20] in g["detalle"] for g in json.loads(crudo)["no_entro"])


def test_cada_valor_variable_de_la_pagina_entra_escapado(demo_generada) -> None:
    rutas, _destino, _ = demo_generada
    conexion = abrir(rutas.base, "demo")
    try:
        datos = exportar.reunir(
            conexion, DIA_DEMO, extractor="determinista", version_contrato="1", semilla=SEMILLA
        )
    finally:
        conexion.close()
    limpio = exportar.renderizar(datos)

    hostil = copy.deepcopy(datos)
    marca = "<i>x</i>"
    hostil["pie"]["extractor"] = marca
    hostil["pie"]["version_contrato"] = marca
    for control in hostil["conciliacion"]["controles"]:
        control["control"] = control["descripcion"] = marca
    for panel in hostil["paneles"]:
        panel["titulo"] = panel["unidad"] = marca
        for fila in panel["por_lote"]:
            fila["lote"] = fila["galpon"] = marca
    for grupo in hostil["no_entro"]:
        for clave in ("archivo", "alcance", "explicacion", "detalle"):
            grupo[clave] = marca
    sucio = exportar.renderizar(hostil)

    assert sucio.count("&lt;i&gt;x&lt;/i&gt;") > 40
    assert "<i>" not in sucio
    assert {e for e, _ in _analizar(sucio).etiquetas} == {e for e, _ in _analizar(limpio).etiquetas}


def test_la_demo_versionada_es_la_que_genera_el_codigo(demo_generada) -> None:
    """docs/ no se edita a mano: tiene que ser igual a lo que sale de `make demo`."""
    _rutas, destino, _ = demo_generada
    for nombre in ARTEFACTOS:
        versionado = RAIZ_REPO / "docs" / nombre
        assert versionado.is_file(), f"falta docs/{nombre}: corre `make demo`"
        assert versionado.read_text(encoding="utf-8") == (destino / nombre).read_text(
            encoding="utf-8"
        ), f"docs/{nombre} está desactualizado: corre `make demo`"


def test_la_demo_no_queda_fuera_del_control_de_versiones() -> None:
    """`datos/` se ignora solo en la raíz; `docs/datos/tablero.json` sí se versiona."""
    reglas = (RAIZ_REPO / ".gitignore").read_text(encoding="utf-8").split()
    assert "/datos/" in reglas
    assert not {"datos/", "docs/", "docs", "*.json", "*.html"} & set(reglas)
