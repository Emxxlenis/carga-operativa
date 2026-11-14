"""Dobles de prueba y constructores de entradas. Nada aquí toca la red."""

from __future__ import annotations

import datetime as dt
import io
import sqlite3
from pathlib import Path

from openpyxl import Workbook

from carga_operativa.config import Rutas
from carga_operativa.extraccion.puerto import ExtraccionNoDisponible
from carga_operativa.sintetico import generar
from carga_operativa.tipos import CorreoTexto

DIA = "2026-11-12"
AHORA = "2026-11-12T23:00:00+00:00"
ENCABEZADOS = list(generar.ENCABEZADOS)


def reloj_fijo() -> str:
    return AHORA


class ExtractorGuionado:
    """Implementa el puerto y devuelve lo que se le indique.

    Simula al modelo que inventa un campo, devuelve un tipo inválido o
    devuelve un texto en vez de una lista.
    """

    nombre = "api"

    def __init__(self, respuesta: object) -> None:
        self.respuesta = respuesta
        self.correos: list[CorreoTexto] = []

    def extraer(self, correo: CorreoTexto) -> object:
        self.correos.append(correo)
        return self.respuesta


class ExtractorCaido:
    """Simula un servicio de extracción que no responde."""

    nombre = "api"

    def extraer(self, correo: CorreoTexto) -> object:
        raise ExtraccionNoDisponible("simulado")


def fila(
    lote: str = "L-001",
    metrica: str = "produccion",
    valor: object = 100.5,
    unidad: object = "kg",
    fecha: object = DIA,
) -> list[object]:
    """Una fila de hoja de cálculo en el orden del encabezado."""
    return [fecha, lote, metrica, valor, unidad]


def registro(
    lote: str = "L-001",
    metrica: str = "mortalidad",
    valor: str = "3",
    unidad: str = "aves",
    fecha: str = DIA,
) -> dict[str, str]:
    return {"fecha": fecha, "lote": lote, "metrica": metrica, "valor": valor, "unidad": unidad}


def xlsx_openpyxl(filas: list[list[object]]) -> bytes:
    """Un libro escrito con openpyxl: admite celdas de fecha y fórmulas."""
    libro = Workbook()
    hoja = libro.active
    for valores in filas:
        hoja.append(valores)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


class Buzon:
    """Escribe archivos en el buzón de un día, como si llegaran de afuera."""

    def __init__(self, rutas: Rutas) -> None:
        self.rutas = rutas

    def carpeta(self, dia: str = DIA) -> Path:
        carpeta = self.rutas.buzon / dia
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta

    def crudo(self, nombre: str, datos: bytes, dia: str = DIA) -> Path:
        ruta = self.carpeta(dia) / nombre
        ruta.write_bytes(datos)
        return ruta

    def xlsx(self, nombre: str, filas: list[list[object]], dia: str = DIA) -> Path:
        return self.crudo(nombre, generar.xlsx_bytes([ENCABEZADOS, *filas]), dia)

    def xml(self, nombre: str, registros: list[dict[str, str]], dia: str = DIA) -> Path:
        return self.crudo(nombre, generar.xml_bytes(registros), dia)

    def eml(self, nombre: str, lineas: list[str], dia: str = DIA) -> Path:
        return self.crudo(nombre, generar.eml_bytes(dia, lineas), dia)


def consultar(rutas: Rutas, sql: str, parametros: tuple[object, ...] = ()) -> list[tuple]:
    """Lectura directa de la base, por fuera de las capas. Solo para comprobar."""
    conexion = sqlite3.connect(rutas.base)
    try:
        return conexion.execute(sql, parametros).fetchall()
    finally:
        conexion.close()


def alterar(rutas: Rutas, sql: str, parametros: tuple[object, ...] = ()) -> None:
    """Escritura directa de la base, por fuera de las capas. Simula una adulteración."""
    conexion = sqlite3.connect(rutas.base)
    try:
        conexion.execute(sql, parametros)
        conexion.commit()
    finally:
        conexion.close()


def volcado(rutas: Rutas) -> str:
    conexion = sqlite3.connect(rutas.base)
    try:
        return "\n".join(conexion.iterdump())
    finally:
        conexion.close()


def motivos_del_dia(rutas: Rutas, dia: str = DIA) -> list[str]:
    return [
        motivo
        for (motivo,) in consultar(
            rutas,
            "SELECT motivo FROM cuarentena WHERE dia_carga = ? ORDER BY cuarentena_id",
            (dia,),
        )
    ]


def fecha_mas(dias: int, base: str = DIA) -> str:
    return (dt.date.fromisoformat(base) + dt.timedelta(days=dias)).isoformat()
