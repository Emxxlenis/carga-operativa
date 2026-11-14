"""Conciliación: lo que entra es igual a lo aceptado más lo rechazado, en cada capa."""

from __future__ import annotations

import pytest

from carga_operativa import conciliacion, cuarentena
from carga_operativa.config import Rutas
from carga_operativa.contrato.motivos import Motivo
from carga_operativa.pronostico import linea_base
from dobles import (
    AHORA,
    DIA,
    Buzon,
    ExtractorCaido,
    alterar,
    consultar,
    fila,
    registro,
)


def _controles(rutas: Rutas, dia: str = DIA) -> dict[str, tuple[float, float, int]]:
    return {
        control: (esperado, obtenido, cuadra)
        for control, esperado, obtenido, cuadra in consultar(
            rutas,
            "SELECT control, esperado, obtenido, cuadra FROM conciliacion_control "
            "WHERE dia_carga = ?",
            (dia,),
        )
    }


def _estado(rutas: Rutas, dia: str = DIA) -> str:
    return consultar(rutas, "SELECT estado FROM conciliacion_dia WHERE dia_carga = ?", (dia,))[0][0]


def _sembrar_historia(buzon: Buzon, correr, dias: tuple[str, ...]) -> None:
    for dia in dias:
        buzon.xlsx("produccion.xlsx", [fila("L-001", fecha=dia, valor=100.0 + int(dia[-2:]))], dia)
        assert correr(dia).cuadra


def test_un_dia_limpio_cuadra_en_los_seis_controles(rutas: Rutas, buzon: Buzon, correr) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001"), fila("L-002", unidad="lb")])
    buzon.xml("mortalidad.xml", [registro("L-001")])

    resultado = correr()

    assert resultado.cuadra and resultado.codigo_salida == 0
    assert _controles(rutas) == {
        "archivos": (2.0, 2.0, 1),
        "filas": (3.0, 3.0, 1),
        "filas_por_archivo": (0.0, 0.0, 1),
        "grano_modelo": (2.0, 2.0, 1),
        "totales_modelo": (0.0, 0.0, 1),
        "totales_mart": (0.0, 0.0, 1),
    }
    assert tuple(c.control for c in resultado.controles) == conciliacion.CONTROLES


def test_conciliacion_detecta_fila_perdida(
    rutas: Rutas, buzon: Buzon, correr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un defecto que descarta una fila rechazada sin registrarla no pasa inadvertido."""
    _sembrar_historia(buzon, correr, ("2026-11-09", "2026-11-10", "2026-11-11"))
    original = cuarentena.registrar

    def registrar_con_defecto(conexion, **kwargs):
        if kwargs["motivo"] == Motivo.UNIDAD_DISTINTA:
            return  # la fila se pierde: ni aceptada ni en cuarentena
        original(conexion, **kwargs)

    monkeypatch.setattr(cuarentena, "registrar", registrar_con_defecto)
    buzon.xlsx("produccion.xlsx", [fila("L-001"), fila("L-002", unidad="lb"), fila("L-003")])

    resultado = correr()

    assert not resultado.cuadra and resultado.codigo_salida == 2
    assert set(resultado.controles_fallidos) == {"filas", "filas_por_archivo"}
    controles = _controles(rutas)
    assert controles["filas"] == (3.0, 2.0, 0)
    assert controles["filas_por_archivo"] == (0.0, 1.0, 0)
    assert _estado(rutas) == "no_cuadra"
    # Sin conciliación no hay pronóstico para ese día.
    assert resultado.filas_pronostico == 0
    assert consultar(
        rutas, "SELECT COUNT(*) FROM pronostico_linea_base WHERE dia_base = ?", (DIA,)
    ) == [(0,)]


def test_conciliacion_no_compensa_entre_archivos(rutas: Rutas, buzon: Buzon, correr) -> None:
    """Un archivo con una fila de más no tapa a otro con una fila de menos."""
    buzon.xlsx("produccion.xlsx", [fila("L-001"), fila("L-002")])
    buzon.xml("mortalidad.xml", [registro("L-001"), registro("L-002")])
    assert correr().cuadra

    ids = dict(consultar(rutas, "SELECT nombre_original, archivo_id FROM landing_archivo"))
    alterar(
        rutas,
        "UPDATE contrato_archivo SET filas_leidas = filas_leidas + 1 WHERE archivo_id = ?",
        (ids["produccion.xlsx"],),
    )
    alterar(
        rutas,
        "UPDATE contrato_archivo SET filas_leidas = filas_leidas - 1 WHERE archivo_id = ?",
        (ids["mortalidad.xml"],),
    )

    resultado = conciliacion.conciliar(rutas.base, DIA, AHORA)

    por_control = {c.control: c for c in resultado.controles}
    # El total del día sigue dando 4 = 4: sumado, el error se compensa.
    assert por_control["filas"].cuadra
    assert (por_control["filas"].esperado, por_control["filas"].obtenido) == (4.0, 4.0)
    # Por archivo, no.
    assert not por_control["filas_por_archivo"].cuadra
    assert por_control["filas_por_archivo"].obtenido == 2.0
    assert resultado.estado == "no_cuadra"
    assert _estado(rutas) == "no_cuadra"


@pytest.mark.parametrize(
    ("sentencia", "fallidos"),
    [
        (
            "UPDATE mart_produccion SET produccion_kg = produccion_kg + 50 WHERE lote = 'L-001'",
            {"totales_mart"},
        ),
        ("UPDATE mart_resumen_dia SET produccion_kg = produccion_kg + 50", {"totales_mart"}),
        ("UPDATE mart_resumen_dia SET lotes_con_produccion = 9", {"totales_mart"}),
        ("DELETE FROM mart_mortalidad WHERE lote = 'L-002'", {"totales_mart"}),
        (
            "INSERT INTO mart_produccion VALUES ('2026-11-12', 'L-006', 'Galpon X', 0.0)",
            {"totales_mart"},
        ),
        (
            "UPDATE hecho_dia_lote SET produccion_kg = produccion_kg - 1 "
            "WHERE lote_id = (SELECT lote_id FROM dim_lote WHERE codigo = 'L-001')",
            {"totales_modelo", "totales_mart"},
        ),
        (
            "UPDATE hecho_dia_lote SET mortalidad_aves = NULL "
            "WHERE lote_id = (SELECT lote_id FROM dim_lote WHERE codigo = 'L-002')",
            {"grano_modelo", "totales_modelo", "totales_mart"},
        ),
    ],
)
def test_conciliacion_detecta_mart_adulterado(
    rutas: Rutas, buzon: Buzon, correr, sentencia: str, fallidos: set[str]
) -> None:
    buzon.xlsx("produccion.xlsx", [fila("L-001"), fila("L-002", valor=90.0)])
    buzon.xml("mortalidad.xml", [registro("L-001"), registro("L-002", valor="4")])
    assert correr().cuadra

    alterar(rutas, sentencia)
    resultado = conciliacion.conciliar(rutas.base, DIA, AHORA)

    assert resultado.estado == "no_cuadra"
    assert set(resultado.fallidos) == fallidos
    assert _estado(rutas) == "no_cuadra"
    with pytest.raises(linea_base.ConciliacionNoCuadra):
        linea_base.ejecutar(rutas.base, DIA, AHORA)


def test_archivo_sin_procesar_no_cuadra(rutas: Rutas, buzon: Buzon, correr) -> None:
    _sembrar_historia(buzon, correr, ("2026-11-09", "2026-11-10", "2026-11-11"))
    buzon.xlsx("produccion.xlsx", [fila("L-001")])
    buzon.eml("ventas.eml", ["ventas lote L-001: 80 kg"])

    resultado = correr(extractor=ExtractorCaido())

    assert not resultado.cuadra
    assert resultado.controles_fallidos == ("archivos",)
    assert _controles(rutas)["archivos"] == (2.0, 1.0, 0)
    pendiente = [a for a in resultado.archivos if a.estado == "pendiente"]
    assert [a.nombre for a in pendiente] == ["ventas.eml"]
    assert consultar(rutas, "SELECT COUNT(*) FROM contrato_archivo") == [(4,)]
    assert consultar(
        rutas, "SELECT COUNT(*) FROM pronostico_linea_base WHERE dia_base = ?", (DIA,)
    ) == [(0,)]

    # No hubo recambio silencioso a otro extractor: nada del correo entró.
    assert consultar(rutas, "SELECT COUNT(*) FROM mart_ventas") == [(0,)]

    # Cuando el servicio vuelve, el mismo archivo se procesa y el día cuadra.
    recuperado = correr()
    assert recuperado.cuadra
    assert (recuperado.recepcion.nuevos, recuperado.recepcion.ya_recibidos) == (0, 2)
    assert consultar(rutas, "SELECT ventas_kg FROM mart_ventas") == [(80.0,)]
    assert recuperado.filas_pronostico > 0


def test_un_dia_que_deja_de_cuadrar_pierde_su_pronostico(
    rutas: Rutas, buzon: Buzon, correr
) -> None:
    _sembrar_historia(buzon, correr, ("2026-11-09", "2026-11-10", "2026-11-11"))
    buzon.xlsx("produccion.xlsx", [fila("L-001")])
    assert correr().filas_pronostico == 7

    buzon.eml("ventas.eml", ["ventas lote L-001: 80 kg"])
    resultado = correr(extractor=ExtractorCaido())

    assert not resultado.cuadra
    assert consultar(
        rutas, "SELECT COUNT(*) FROM pronostico_linea_base WHERE dia_base = ?", (DIA,)
    ) == [(0,)], "el pronóstico anterior del día se borró antes de recalcular"


def test_un_dia_sin_archivos_cuadra_en_cero(rutas: Rutas, correr) -> None:
    resultado = correr()
    assert resultado.cuadra
    assert all(c.esperado == 0 and c.obtenido == 0 for c in resultado.controles)
    assert resultado.filas_pronostico == 0
