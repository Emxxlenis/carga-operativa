"""Cuarentena: registrar y listar. No hay función de borrar ni de editar."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .contrato import motivos
from .db.conexion import ConexionCapa

NIVEL_ARCHIVO = "archivo"
NIVEL_FILA = "fila"


@dataclass(frozen=True)
class Rechazo:
    nombre_archivo: str
    nivel: str
    n_fila: int
    motivo: str
    detalle: str


def registrar(
    conexion: ConexionCapa,
    *,
    dia_carga: str,
    nombre_archivo: str,
    archivo_id: int | None,
    n_fila: int,
    motivo: str,
    campos: Mapping[str, object],
    fila_cruda: Mapping[object, object] | None,
    ahora: str,
) -> None:
    """Inserta un rechazo. ``n_fila`` 0 significa el archivo completo.

    El detalle lo redacta ``motivos.redactar``; quien llama entrega solo el
    código del motivo y los campos de la plantilla. Volver a registrar el mismo
    rechazo no duplica.
    """
    if not motivos.es_motivo(motivo):
        raise ValueError("Motivo fuera de la lista cerrada.")
    nivel = NIVEL_ARCHIVO if n_fila == 0 else NIVEL_FILA
    if motivos.es_de_archivo(motivo) != (nivel == NIVEL_ARCHIVO):
        raise ValueError("El motivo no corresponde al nivel del rechazo.")
    detalle = motivos.redactar(motivo, **campos)
    fila_json = None
    if fila_cruda is not None:
        fila_json = json.dumps(motivos.fila_segura(fila_cruda), ensure_ascii=False, sort_keys=True)
    conexion.execute(
        """
        INSERT INTO cuarentena
          (dia_carga, nombre_archivo, archivo_id, nivel, n_fila, motivo, detalle,
           fila_cruda_json, creado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (dia_carga, nombre_archivo, archivo_id, nivel, n_fila, motivo, detalle, fila_json, ahora),
    )


def listar(conexion: ConexionCapa, dia_carga: str) -> list[Rechazo]:
    """Rechazos de un día, en orden estable."""
    filas = conexion.execute(
        """
        SELECT nombre_archivo, nivel, n_fila, motivo, detalle
        FROM cuarentena
        WHERE dia_carga = ?
        ORDER BY nombre_archivo, n_fila, cuarentena_id
        """,
        (dia_carga,),
    ).fetchall()
    return [Rechazo(*fila) for fila in filas]
