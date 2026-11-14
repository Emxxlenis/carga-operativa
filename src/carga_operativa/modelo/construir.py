"""Modelo: hechos y dimensiones, una fila por fecha y lote.

Solo SQL sobre las filas aceptadas. No lee archivos ni conoce formatos. Las
filas aceptadas nunca desaparecen, así que alcanza con insertar o actualizar
por clave primaria: no hace falta borrar.
"""

from __future__ import annotations

import datetime as dt

from ..db.conexion import RutaBase, abrir, transaccion


def construir(ruta_base: RutaBase, dia: str) -> int:
    """Construye el modelo de un día. Devuelve cuántas filas de hecho tiene."""
    fecha = dt.date.fromisoformat(dia)
    _, semana_iso, dia_semana = fecha.isocalendar()
    conexion = abrir(ruta_base, "modelo")
    try:
        with transaccion(conexion):
            conexion.execute(
                """
                INSERT INTO dim_lote (codigo, galpon, fecha_ingreso, aves_iniciales)
                SELECT codigo, galpon, fecha_ingreso, aves_iniciales
                FROM ref_lote
                WHERE true
                ON CONFLICT (codigo) DO UPDATE SET
                  galpon = excluded.galpon,
                  fecha_ingreso = excluded.fecha_ingreso,
                  aves_iniciales = excluded.aves_iniciales
                """
            )
            hay_filas = conexion.execute(
                "SELECT 1 FROM contrato_fila_aceptada WHERE fecha = ? LIMIT 1", (dia,)
            ).fetchone()
            if hay_filas:
                conexion.execute(
                    """
                    INSERT INTO dim_fecha (fecha, anio, mes, dia, dia_semana, semana_iso)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (fecha) DO NOTHING
                    """,
                    (dia, fecha.year, fecha.month, fecha.day, dia_semana, semana_iso),
                )
            conexion.execute(
                """
                INSERT INTO hecho_dia_lote
                  (fecha, lote_id, produccion_kg, mortalidad_aves, ventas_kg)
                SELECT
                  a.fecha,
                  d.lote_id,
                  SUM(CASE WHEN a.metrica = 'produccion' THEN a.valor END),
                  CAST(SUM(CASE WHEN a.metrica = 'mortalidad' THEN a.valor END) AS INTEGER),
                  SUM(CASE WHEN a.metrica = 'ventas' THEN a.valor END)
                FROM contrato_fila_aceptada AS a
                JOIN dim_lote AS d ON d.codigo = a.lote
                WHERE a.fecha = ?
                GROUP BY a.fecha, d.lote_id
                ON CONFLICT (fecha, lote_id) DO UPDATE SET
                  produccion_kg = excluded.produccion_kg,
                  mortalidad_aves = excluded.mortalidad_aves,
                  ventas_kg = excluded.ventas_kg
                """,
                (dia,),
            )
        return conexion.execute(
            "SELECT COUNT(*) FROM hecho_dia_lote WHERE fecha = ?", (dia,)
        ).fetchone()[0]
    finally:
        conexion.close()
