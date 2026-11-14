"""Pronóstico: solo lee el mart, solo corre con conciliación, y es una línea base."""

from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest

from carga_operativa import conciliacion, pipeline
from carga_operativa.config import Rutas
from carga_operativa.db.conexion import AccesoCapaProhibido, abrir
from carga_operativa.pronostico import linea_base
from dobles import AHORA, DIA, Buzon, ExtractorCaido, alterar, consultar, fecha_mas, fila

TABLAS_CRUDAS = (
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
    "conciliacion_control",
)


def _dia(resta: int) -> str:
    return fecha_mas(-resta)


def _cargar(buzon: Buzon, correr, dia: str, valor: float) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001", fecha=dia, valor=valor)], dia)
    assert correr(dia).cuadra


def _pronostico(rutas: Rutas, dia: str = DIA) -> list[tuple]:
    return consultar(
        rutas,
        "SELECT fecha_objetivo, metrica, valor, metodo, n_observaciones, mae_retro "
        "FROM pronostico_linea_base WHERE dia_base = ? ORDER BY 1, 2",
        (dia,),
    )


@pytest.mark.parametrize("tabla", TABLAS_CRUDAS)
def test_pronostico_no_puede_leer_capa_cruda(
    rutas: Rutas, buzon: Buzon, correr, tabla: str
) -> None:
    _cargar(buzon, correr, DIA, 100.0)
    con = abrir(rutas.base, "pronostico")
    try:
        with pytest.raises(AccesoCapaProhibido) as error:
            con.execute(f"SELECT * FROM {tabla}").fetchall()
        assert (error.value.capa, error.value.objeto) == ("pronostico", tabla)
        # Tampoco a través de un JOIN con una tabla permitida, ni contando.
        with pytest.raises(AccesoCapaProhibido):
            con.execute(
                f"SELECT m.fecha FROM mart_resumen_dia m JOIN {tabla} t ON 1 = 1"
            ).fetchall()
        with pytest.raises(AccesoCapaProhibido):
            con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchall()
        # Lo que sí lee.
        assert con.execute("SELECT COUNT(*) FROM mart_resumen_dia").fetchone() == (1,)
        assert con.execute("SELECT estado FROM conciliacion_dia").fetchall() == [("cuadra",)]
    finally:
        con.close()


def test_pronostico_falla_si_su_codigo_consulta_una_tabla_cruda(
    rutas: Rutas, buzon: Buzon, correr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si alguien cambia la historia para leer el hecho, la corrida falla; no degrada."""
    for resta in range(3, -1, -1):
        _cargar(buzon, correr, _dia(resta), 100.0)
    antes = _pronostico(rutas)

    def historia_desde_el_hecho(conexion, _dia_base):
        conexion.execute("SELECT fecha, produccion_kg FROM hecho_dia_lote").fetchall()
        return {}

    monkeypatch.setattr(linea_base, "_historia", historia_desde_el_hecho)
    with pytest.raises(AccesoCapaProhibido):
        linea_base.ejecutar(rutas.base, DIA, AHORA)
    assert _pronostico(rutas) == antes, "la transacción se deshizo"


def test_el_modulo_de_pronostico_solo_nombra_sus_tablas() -> None:
    fuente = inspect.getsource(linea_base)
    cadenas = [
        nodo.value
        for nodo in ast.walk(ast.parse(fuente))
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
    ]
    sql = " ".join(c for c in cadenas if "SELECT" in c or "INSERT" in c or "DELETE" in c)
    for tabla in TABLAS_CRUDAS:
        assert tabla not in sql, tabla
    assert "mart_resumen_dia" in sql and "conciliacion_dia" in sql


@pytest.mark.parametrize("estado", [None, "pendiente", "no_cuadra"])
def test_pronostico_no_corre_sin_conciliacion(
    rutas: Rutas, buzon: Buzon, correr, estado: str | None
) -> None:
    for resta in range(4, -1, -1):
        _cargar(buzon, correr, _dia(resta), 100.0)
    assert len(_pronostico(rutas)) == 7
    linea_base.borrar(rutas.base, DIA)

    if estado is None:
        alterar(rutas, "DELETE FROM conciliacion_control WHERE dia_carga = ?", (DIA,))
        alterar(rutas, "DELETE FROM conciliacion_dia WHERE dia_carga = ?", (DIA,))
    else:
        alterar(rutas, "UPDATE conciliacion_dia SET estado = ? WHERE dia_carga = ?", (estado, DIA))

    # La compuerta está dentro de ejecutar: llamarlo directo tampoco escribe.
    with pytest.raises(linea_base.ConciliacionNoCuadra) as error:
        linea_base.ejecutar(rutas.base, DIA, AHORA)
    assert (error.value.dia, error.value.estado) == (DIA, estado)
    assert _pronostico(rutas) == []


def test_marcar_pendiente_cierra_la_compuerta_mientras_se_procesa(
    rutas: Rutas, buzon: Buzon, correr
) -> None:
    for resta in range(3, -1, -1):
        _cargar(buzon, correr, _dia(resta), 100.0)

    conciliacion.marcar_pendiente(rutas.base, DIA, AHORA)

    with pytest.raises(linea_base.ConciliacionNoCuadra):
        linea_base.ejecutar(rutas.base, DIA, AHORA)


def test_un_dia_que_se_interrumpe_a_medias_queda_pendiente_y_sin_pronostico(
    rutas: Rutas, buzon: Buzon, correr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si la corrida muere entre capas, no sobrevive el 'cuadra' de la corrida anterior."""
    for resta in range(3, -1, -1):
        _cargar(buzon, correr, _dia(resta), 100.0)
    assert len(_pronostico(rutas)) == 7

    class Corte(RuntimeError):
        pass

    def mart_que_falla(_ruta_base, _dia):
        raise Corte

    monkeypatch.setattr(pipeline.mart, "construir", mart_que_falla)
    buzon.xlsx("produccion_tarde.xlsx", [fila("L-002", valor=50.0)])
    with pytest.raises(Corte):
        correr()

    # El contrato y el modelo ya tienen la fila nueva; el mart, no.
    assert consultar(
        rutas, "SELECT COUNT(*) FROM contrato_fila_aceptada WHERE fecha = ?", (DIA,)
    ) == [(2,)]
    assert consultar(
        rutas, "SELECT produccion_kg FROM mart_resumen_dia WHERE fecha = ?", (DIA,)
    ) == [(100.0,)]
    assert consultar(rutas, "SELECT estado FROM conciliacion_dia WHERE dia_carga = ?", (DIA,)) == [
        ("pendiente",)
    ]
    assert _pronostico(rutas) == []
    with pytest.raises(linea_base.ConciliacionNoCuadra):
        linea_base.ejecutar(rutas.base, DIA, AHORA)

    # Al reintentar, el día termina y vuelve a cuadrar con el dato completo.
    monkeypatch.undo()
    assert correr().cuadra
    assert consultar(
        rutas, "SELECT produccion_kg FROM mart_resumen_dia WHERE fecha = ?", (DIA,)
    ) == [(150.0,)]
    assert len(_pronostico(rutas)) == 7


def test_pronostico_excluye_dias_no_conciliados(rutas: Rutas, buzon: Buzon, correr) -> None:
    valores = {5: 100.0, 4: 100.0, 3: 100.0, 2: 9000.0, 1: 100.0, 0: 100.0}
    for resta, valor in sorted(valores.items(), reverse=True):
        dia = _dia(resta)
        buzon.xlsx("produccion.xlsx", [fila("L-001", fecha=dia, valor=valor)], dia)
        if resta == 2:
            # Ese día queda un correo sin procesar: no cuadra.
            buzon.eml("ventas.eml", ["ventas lote L-001: 80 kg"], dia)
            assert not correr(dia, extractor=ExtractorCaido()).cuadra
        else:
            assert correr(dia).cuadra

    # El día malo está en el mart, con su valor atípico...
    assert consultar(
        rutas, "SELECT produccion_kg FROM mart_resumen_dia WHERE fecha = ?", (_dia(2),)
    ) == [(9000.0,)]
    # ...pero la línea base no lo usa: 5 observaciones y promedio de 100.
    filas = _pronostico(rutas)
    assert len(filas) == 7
    assert {(valor, metodo, n) for _f, _m, valor, metodo, n, _mae in filas} == {
        (100.0, "promedio_movil_7", 5)
    }
    # Y ese día tampoco tiene pronóstico propio.
    assert _pronostico(rutas, _dia(2)) == []


def test_menos_de_tres_dias_conciliados_no_escribe_nada(rutas: Rutas, buzon: Buzon, correr) -> None:
    _cargar(buzon, correr, _dia(1), 100.0)
    _cargar(buzon, correr, DIA, 100.0)

    assert linea_base.ejecutar(rutas.base, DIA, AHORA) == 0
    assert _pronostico(rutas) == []


def test_pronostico_es_linea_base_reproducible(rutas: Rutas, buzon: Buzon, correr) -> None:
    for resta in range(20, -1, -1):
        dia = dt.date.fromisoformat(_dia(resta))
        _cargar(buzon, correr, dia.isoformat(), 100.0 + 10 * dia.weekday())

    primera = _pronostico(rutas)
    assert linea_base.ejecutar(rutas.base, DIA, "2030-01-01T00:00:00+00:00") == 7
    segunda = _pronostico(rutas)

    assert primera == segunda, "mismos datos, misma línea base"
    assert len(primera) == 7, "horizonte de 7 días, una métrica con datos"
    base = dt.date.fromisoformat(DIA)
    for paso, (fecha, metrica, valor, metodo, n, mae) in enumerate(primera, start=1):
        objetivo = base + dt.timedelta(days=paso)
        assert fecha == objetivo.isoformat()
        assert metrica == "produccion"
        # Estacional: el valor del mismo día de la semana.
        assert valor == 100.0 + 10 * objetivo.weekday()
        assert metodo == "estacional_semanal"
        assert n == 21
        assert mae == 0.0
    assert {metodo for *_resto, metodo, _n, _mae in primera} <= {
        "promedio_movil_7",
        "estacional_semanal",
    }


def test_calcular_es_puro_y_elige_el_metodo_por_cantidad_de_historia() -> None:
    base = dt.date(2026, 11, 12)

    def serie(n: int) -> dict[dt.date, float]:
        return {base - dt.timedelta(days=i): float(10 + i) for i in range(n)}

    assert linea_base.calcular(serie(0), base, "ventas") == []
    assert linea_base.calcular(serie(2), base, "ventas") == []

    tres = linea_base.calcular(serie(3), base, "ventas")
    assert len(tres) == 7
    assert {p.metodo for p in tres} == {"promedio_movil_7"}
    assert {p.valor for p in tres} == {11.0}
    assert tres == linea_base.calcular(serie(3), base, "ventas")

    trece = linea_base.calcular(serie(13), base, "ventas")
    assert {p.metodo for p in trece} == {"promedio_movil_7"}
    assert {p.valor for p in trece} == {13.0}  # promedio de las últimas 7: 10..16

    catorce = linea_base.calcular(serie(14), base, "ventas")
    assert {p.metodo for p in catorce} == {"estacional_semanal"}
    assert [p.fecha_objetivo for p in catorce] == [
        base + dt.timedelta(days=paso) for paso in range(1, 8)
    ]
    assert all(p.n_observaciones == 14 for p in catorce)


def test_estacional_sin_historia_de_ese_dia_cae_a_promedio_y_lo_rotula() -> None:
    base = dt.date(2026, 11, 12)  # jueves
    # 14 observaciones, pero ningún sábado ni domingo.
    serie: dict[dt.date, float] = {}
    fecha = base
    while len(serie) < 14:
        if fecha.weekday() < 5:
            serie[fecha] = 50.0
        fecha -= dt.timedelta(days=1)

    puntos = {p.fecha_objetivo.weekday(): p for p in linea_base.calcular(serie, base, "ventas")}

    assert puntos[5].metodo == puntos[6].metodo == "promedio_movil_7"
    assert puntos[0].metodo == "estacional_semanal"


def test_reejecutar_el_dia_reemplaza_su_pronostico_sin_duplicar(
    rutas: Rutas, buzon: Buzon, correr
) -> None:
    for resta in range(3, -1, -1):
        _cargar(buzon, correr, _dia(resta), 100.0)
    antes = _pronostico(rutas)
    assert len(antes) == 7

    assert correr().filas_pronostico == 7

    assert _pronostico(rutas) == antes
    # El pronóstico de otros días base no se toca.
    assert len(_pronostico(rutas, _dia(1))) == 7
