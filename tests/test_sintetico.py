"""Datos sintéticos: los genera el repositorio, con semilla, y siempre salen iguales."""

from __future__ import annotations

import ast
import hashlib
import inspect
import random
from pathlib import Path

from carga_operativa.config import DIA_DEMO, DIAS_HISTORIA, SEMILLA
from carga_operativa.contrato import aplicar
from carga_operativa.sintetico import generar
from dobles import consultar

LOTES = [codigo for codigo, *_resto in aplicar.leer_catalogo()]


def _huellas(raiz: Path) -> dict[str, str]:
    return {
        str(ruta.relative_to(raiz)): hashlib.sha256(ruta.read_bytes()).hexdigest()
        for ruta in sorted(raiz.rglob("*"))
        if ruta.is_file()
    }


def test_generador_con_misma_semilla_es_reproducible(tmp_path: Path) -> None:
    random.seed(1)
    dias_a = generar.generar(tmp_path / "a", LOTES)
    random.seed(2)  # el generador global no influye
    dias_b = generar.generar(tmp_path / "b", LOTES)

    assert dias_a == dias_b
    assert len(dias_a) == DIAS_HISTORIA and dias_a[-1] == DIA_DEMO
    huellas = _huellas(tmp_path / "a")
    assert huellas == _huellas(tmp_path / "b"), "misma semilla, mismos bytes"
    assert len(huellas) == 3 * DIAS_HISTORIA + 1  # tres archivos por día y el anexo con macros

    generar.generar(tmp_path / "c", LOTES, semilla=SEMILLA + 1)
    assert _huellas(tmp_path / "c") != huellas, "otra semilla, otros datos"

    # Volver a generar sobre el mismo directorio no cambia nada.
    generar.generar(tmp_path / "a", LOTES)
    assert _huellas(tmp_path / "a") == huellas


def test_los_bytes_del_dia_de_la_demo_no_cambian_entre_corridas_ni_maquinas() -> None:
    """Huella fija: si cambia, cambió el generador y hay que regenerar docs/."""
    contenido = generar.contenido_dia(DIA_DEMO, LOTES, con_defectos=True)
    resumen = hashlib.sha256()
    for nombre in sorted(contenido):
        resumen.update(nombre.encode())
        resumen.update(hashlib.sha256(contenido[nombre]).digest())
    assert resumen.hexdigest() == HUELLA_DIA_DEMO


HUELLA_DIA_DEMO = "e62322260a0f0d14bf7fe40ab35fa25f63c3c59677702daff4acf17f52473a0f"


def test_generador_no_usa_reloj_ni_random_global() -> None:
    arbol = ast.parse(inspect.getsource(generar))
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados |= {alias.name.split(".")[0] for alias in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
            importados.add((nodo.module or "").split(".")[0])
            if nodo.module == "random":
                assert [alias.name for alias in nodo.names] == ["Random"]
    assert not importados & {"time", "uuid", "secrets", "os", "socket", "urllib", "openpyxl"}
    assert "random" in importados

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Attribute):
            assert nodo.attr not in ("now", "today", "utcnow", "urandom", "getrandbits"), nodo.attr
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "Random"
        ):
            assert nodo.args, "Random() sin semilla usaría el reloj del sistema"


def test_los_datos_sinteticos_no_parecen_reales() -> None:
    contenido = generar.contenido_dia(DIA_DEMO, LOTES, con_defectos=True)
    correo = contenido[f"ventas_{DIA_DEMO}.eml"].decode("utf-8")
    assert "granja-demo.example" in correo  # dominio reservado para ejemplos
    assert "demostracion" in correo.lower()
    dominios = {
        linea.split("@")[1].split(">")[0]
        for linea in correo.splitlines()
        if "@" in linea and linea.split(":")[0] in ("From", "To", "Message-ID")
    }
    assert dominios == {"granja-demo.example"}


def test_solo_el_dia_de_la_demo_trae_defectos_y_son_los_seis_declarados(demo_generada) -> None:
    rutas, _destino, resultados = demo_generada

    assert consultar(rutas, "SELECT DISTINCT dia_carga FROM cuarentena") == [(DIA_DEMO,)]
    assert consultar(
        rutas,
        "SELECT nombre_archivo, nivel, motivo FROM cuarentena ORDER BY nombre_archivo, n_fila",
    ) == [
        (f"mortalidad_{DIA_DEMO}.xml", "fila", "fecha_invalida"),
        (f"mortalidad_{DIA_DEMO}.xml", "fila", "lote_desconocido"),
        (f"produccion_{DIA_DEMO}.xlsx", "fila", "formula_no_evaluada"),
        (f"produccion_{DIA_DEMO}.xlsx", "fila", "unidad_distinta"),
        (f"produccion_anexo_{DIA_DEMO}.xlsx", "archivo", "excel_con_macros"),
        (f"ventas_{DIA_DEMO}.eml", "fila", "columna_faltante"),
    ]
    # El día de la demo cuadra porque lo rechazado está contado.
    demo = resultados[-1]
    assert demo.dia == DIA_DEMO and demo.cuadra
    assert sum(a.filas_leidas for a in demo.archivos) == 19
    assert sum(a.aceptadas for a in demo.archivos) == 14
    assert sum(a.rechazadas for a in demo.archivos) == 5
    assert [a.motivo_archivo for a in demo.archivos if a.estado == "rechazado"] == [
        "excel_con_macros"
    ]
    assert demo.filas_pronostico == 21
    # Los días anteriores entran completos: seis lotes, tres métricas.
    assert consultar(
        rutas, "SELECT COUNT(*) FROM contrato_fila_aceptada WHERE fecha <> ?", (DIA_DEMO,)
    ) == [((DIAS_HISTORIA - 1) * 18,)]
