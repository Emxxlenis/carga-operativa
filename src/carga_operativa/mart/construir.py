"""Mart: tablas del tablero desde el modelo. Solo SQL.

Una tabla por métrica y un resumen por día. No hay columnas de formato ni de
archivo: la unidad va en el nombre de la columna.
"""

from __future__ import annotations

from ..db.conexion import RutaBase, abrir, transaccion

# (tabla del mart, columna de medida). Identificadores fijos, no datos.
_MEDIDAS = (
    ("mart_produccion", "produccion_kg"),
    ("mart_mortalidad", "mortalidad_aves"),
    ("mart_ventas", "ventas_kg"),
)


def construir(ruta_base: RutaBase, dia: str) -> None:
    conexion = abrir(ruta_base, "mart")
    try:
        with transaccion(conexion):
            for tabla, medida in _MEDIDAS:
                conexion.execute(
                    f"""
                    INSERT INTO {tabla} (fecha, lote, galpon, {medida})
                    SELECT h.fecha, d.codigo, d.galpon, h.{medida}
                    FROM hecho_dia_lote AS h
                    JOIN dim_lote AS d ON d.lote_id = h.lote_id
                    WHERE h.fecha = ? AND h.{medida} IS NOT NULL
                    ON CONFLICT (fecha, lote) DO UPDATE SET
                      galpon = excluded.galpon,
                      {medida} = excluded.{medida}
                    """,
                    (dia,),
                )
            conexion.execute(
                """
                INSERT INTO mart_resumen_dia
                  (fecha, produccion_kg, mortalidad_aves, ventas_kg,
                   lotes_con_produccion, lotes_con_mortalidad, lotes_con_ventas)
                SELECT
                  h.fecha,
                  SUM(h.produccion_kg),
                  SUM(h.mortalidad_aves),
                  SUM(h.ventas_kg),
                  COUNT(h.produccion_kg),
                  COUNT(h.mortalidad_aves),
                  COUNT(h.ventas_kg)
                FROM hecho_dia_lote AS h
                WHERE h.fecha = ?
                GROUP BY h.fecha
                ON CONFLICT (fecha) DO UPDATE SET
                  produccion_kg = excluded.produccion_kg,
                  mortalidad_aves = excluded.mortalidad_aves,
                  ventas_kg = excluded.ventas_kg,
                  lotes_con_produccion = excluded.lotes_con_produccion,
                  lotes_con_mortalidad = excluded.lotes_con_mortalidad,
                  lotes_con_ventas = excluded.lotes_con_ventas
                """,
                (dia,),
            )
    finally:
        conexion.close()
