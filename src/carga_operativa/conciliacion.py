"""Conciliación del día: el auditor.

No es una capa. Cuenta en todas y escribe solo ``conciliacion_*``. El día
cuadra solo si los seis controles cuadran.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db.conexion import ConexionCapa, RutaBase, abrir, transaccion

PENDIENTE = "pendiente"
CUADRA = "cuadra"
NO_CUADRA = "no_cuadra"

CONTROLES = (
    "archivos",
    "filas",
    "filas_por_archivo",
    "grano_modelo",
    "totales_modelo",
    "totales_mart",
)

TOLERANCIA = 0.001

# (métrica, tabla del mart, columna de medida, columna de conteo del resumen).
# Identificadores fijos, no datos.
_METRICAS = (
    ("produccion", "mart_produccion", "produccion_kg", "lotes_con_produccion"),
    ("mortalidad", "mart_mortalidad", "mortalidad_aves", "lotes_con_mortalidad"),
    ("ventas", "mart_ventas", "ventas_kg", "lotes_con_ventas"),
)


@dataclass(frozen=True)
class Control:
    control: str
    esperado: float
    obtenido: float

    @property
    def cuadra(self) -> bool:
        return abs(self.esperado - self.obtenido) <= TOLERANCIA


@dataclass(frozen=True)
class Resultado:
    dia: str
    estado: str
    controles: tuple[Control, ...]

    @property
    def fallidos(self) -> tuple[str, ...]:
        return tuple(c.control for c in self.controles if not c.cuadra)


def _uno(conexion: ConexionCapa, sql: str, dia: str) -> float:
    valor = conexion.execute(sql, (dia,)).fetchone()[0]
    return float(valor or 0)


def _distinto(a: float, b: float) -> bool:
    return abs(a - b) > TOLERANCIA


def _control_archivos(conexion: ConexionCapa, dia: str) -> Control:
    esperado = _uno(conexion, "SELECT COUNT(*) FROM landing_archivo WHERE dia_carga = ?", dia)
    obtenido = _uno(
        conexion,
        """
        SELECT COUNT(*)
        FROM landing_archivo AS l
        JOIN contrato_archivo AS c ON c.archivo_id = l.archivo_id
        WHERE l.dia_carga = ?
        """,
        dia,
    )
    return Control("archivos", esperado, obtenido)


def _control_filas(conexion: ConexionCapa, dia: str) -> Control:
    esperado = _uno(
        conexion,
        """
        SELECT SUM(c.filas_leidas)
        FROM contrato_archivo AS c
        JOIN landing_archivo AS l ON l.archivo_id = c.archivo_id
        WHERE l.dia_carga = ?
        """,
        dia,
    )
    aceptadas = _uno(
        conexion,
        """
        SELECT COUNT(*)
        FROM contrato_fila_aceptada AS a
        JOIN landing_archivo AS l ON l.archivo_id = a.archivo_id
        WHERE l.dia_carga = ?
        """,
        dia,
    )
    rechazadas = _uno(
        conexion,
        """
        SELECT COUNT(*)
        FROM cuarentena AS q
        JOIN landing_archivo AS l ON l.archivo_id = q.archivo_id
        WHERE l.dia_carga = ? AND q.nivel = 'fila'
        """,
        dia,
    )
    return Control("filas", esperado, aceptadas + rechazadas)


def _control_filas_por_archivo(conexion: ConexionCapa, dia: str) -> Control:
    """Cuenta archivos que no cuadran solos: un sobrante no compensa un faltante."""
    obtenido = _uno(
        conexion,
        """
        SELECT COUNT(*)
        FROM (
          SELECT
            c.estado,
            c.filas_leidas,
            (SELECT COUNT(*) FROM contrato_fila_aceptada AS a
              WHERE a.archivo_id = c.archivo_id) AS aceptadas,
            (SELECT COUNT(*) FROM cuarentena AS q
              WHERE q.archivo_id = c.archivo_id AND q.nivel = 'fila') AS rechazadas,
            (SELECT COUNT(*) FROM cuarentena AS q
              WHERE q.archivo_id = c.archivo_id AND q.nivel = 'archivo') AS rechazos_archivo
          FROM contrato_archivo AS c
          JOIN landing_archivo AS l ON l.archivo_id = c.archivo_id
          WHERE l.dia_carga = ?
        )
        WHERE filas_leidas <> aceptadas + rechazadas
           OR (estado = 'rechazado' AND rechazos_archivo <> 1)
           OR (estado = 'procesado' AND rechazos_archivo <> 0)
        """,
        dia,
    )
    return Control("filas_por_archivo", 0, obtenido)


def _control_grano_modelo(conexion: ConexionCapa, dia: str) -> Control:
    esperado = _uno(conexion, "SELECT COUNT(*) FROM contrato_fila_aceptada WHERE fecha = ?", dia)
    obtenido = _uno(
        conexion,
        """
        SELECT SUM(
          (produccion_kg IS NOT NULL) + (mortalidad_aves IS NOT NULL) + (ventas_kg IS NOT NULL)
        )
        FROM hecho_dia_lote
        WHERE fecha = ?
        """,
        dia,
    )
    return Control("grano_modelo", esperado, obtenido)


def _control_totales_modelo(conexion: ConexionCapa, dia: str) -> Control:
    distintas = 0
    for metrica, _tabla, medida, _conteo in _METRICAS:
        aceptado = conexion.execute(
            "SELECT SUM(valor) FROM contrato_fila_aceptada WHERE fecha = ? AND metrica = ?",
            (dia, metrica),
        ).fetchone()[0]
        hecho = _uno(conexion, f"SELECT SUM({medida}) FROM hecho_dia_lote WHERE fecha = ?", dia)
        if _distinto(float(aceptado or 0), hecho):
            distintas += 1
    return Control("totales_modelo", 0, distintas)


def _control_totales_mart(conexion: ConexionCapa, dia: str) -> Control:
    distintas = 0
    for _metrica, tabla, medida, conteo in _METRICAS:
        total_hecho = _uno(
            conexion, f"SELECT SUM({medida}) FROM hecho_dia_lote WHERE fecha = ?", dia
        )
        filas_hecho = _uno(
            conexion, f"SELECT COUNT({medida}) FROM hecho_dia_lote WHERE fecha = ?", dia
        )
        total_mart = _uno(conexion, f"SELECT SUM({medida}) FROM {tabla} WHERE fecha = ?", dia)
        filas_mart = _uno(conexion, f"SELECT COUNT(*) FROM {tabla} WHERE fecha = ?", dia)
        total_resumen = _uno(
            conexion, f"SELECT SUM({medida}) FROM mart_resumen_dia WHERE fecha = ?", dia
        )
        filas_resumen = _uno(
            conexion, f"SELECT SUM({conteo}) FROM mart_resumen_dia WHERE fecha = ?", dia
        )
        if (
            _distinto(total_hecho, total_mart)
            or _distinto(filas_hecho, filas_mart)
            or _distinto(total_hecho, total_resumen)
            or _distinto(filas_hecho, filas_resumen)
        ):
            distintas += 1
    return Control("totales_mart", 0, distintas)


def marcar_pendiente(ruta_base: RutaBase, dia: str, ahora: str) -> None:
    """Deja el día en 'pendiente' mientras se procesa. Con eso el pronóstico no corre."""
    conexion = abrir(ruta_base, "conciliacion")
    try:
        with transaccion(conexion):
            _guardar_estado(conexion, dia, PENDIENTE, ahora)
    finally:
        conexion.close()


def _guardar_estado(conexion: ConexionCapa, dia: str, estado: str, ahora: str) -> None:
    conexion.execute(
        """
        INSERT INTO conciliacion_dia (dia_carga, estado, calculado_en)
        VALUES (?, ?, ?)
        ON CONFLICT (dia_carga) DO UPDATE SET
          estado = excluded.estado,
          calculado_en = excluded.calculado_en
        """,
        (dia, estado, ahora),
    )


def conciliar(ruta_base: RutaBase, dia: str, ahora: str) -> Resultado:
    """Calcula los seis controles del día y guarda el estado."""
    conexion = abrir(ruta_base, "conciliacion")
    try:
        with transaccion(conexion):
            controles = (
                _control_archivos(conexion, dia),
                _control_filas(conexion, dia),
                _control_filas_por_archivo(conexion, dia),
                _control_grano_modelo(conexion, dia),
                _control_totales_modelo(conexion, dia),
                _control_totales_mart(conexion, dia),
            )
            estado = CUADRA if all(c.cuadra for c in controles) else NO_CUADRA
            _guardar_estado(conexion, dia, estado, ahora)
            conexion.executemany(
                """
                INSERT INTO conciliacion_control (dia_carga, control, esperado, obtenido, cuadra)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (dia_carga, control) DO UPDATE SET
                  esperado = excluded.esperado,
                  obtenido = excluded.obtenido,
                  cuadra = excluded.cuadra
                """,
                [(dia, c.control, c.esperado, c.obtenido, int(c.cuadra)) for c in controles],
            )
        return Resultado(dia=dia, estado=estado, controles=controles)
    finally:
        conexion.close()
