"""Recepción: del buzón a landing, por contenido.

Es el único módulo que abre archivos de entrada. Copia cada archivo regular a
``landing/<día>/<sha256>.<ext>`` en modo solo lectura y lo registra. Lo que no
es un archivo regular no se copia ni se sigue.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .. import cuarentena
from ..config import Rutas
from ..db.conexion import abrir, transaccion
from ..tipos import RechazoArchivo
from .rutas import RutaNoConfinada, resolver_confinada

FORMATOS = ("xlsx", "xml", "eml")
MAX_NOMBRE = 120
_BLOQUE = 64 * 1024
_EXTENSION = re.compile(r"[a-z0-9]{1,8}")
_NO_SEGUIR = getattr(os, "O_NOFOLLOW", 0)
# Abrir una tubería con nombre sin esto dejaría el proceso esperando a un escritor.
_NO_BLOQUEAR = getattr(os, "O_NONBLOCK", 0)
_MOTIVO_NO_REGULAR = "entrada_no_regular"
_MOTIVO_ALTERADO = "archivo_alterado"


@dataclass(frozen=True)
class ResumenRecepcion:
    nuevos: int = 0
    ya_recibidos: int = 0
    no_regulares: int = 0


def sanear_nombre(nombre: str) -> str:
    """Nombre solo para mostrar: sin separadores ni caracteres de control."""
    limpio = "".join(
        caracter if caracter.isprintable() and caracter not in "/\\" else "_" for caracter in nombre
    ).strip()
    return limpio[:MAX_NOMBRE] or "sin_nombre"


def _extension(nombre: str) -> str:
    sufijo = Path(nombre).suffix.lower().lstrip(".")
    return sufijo if _EXTENSION.fullmatch(sufijo) else "bin"


def _copiar_con_huella(origen: str, temporal: Path) -> tuple[str, int] | None:
    """Copia el archivo y devuelve (sha256, bytes). None si no es regular."""
    try:
        descriptor = os.open(origen, os.O_RDONLY | _NO_SEGUIR | _NO_BLOQUEAR)
    except OSError:
        # Entre el listado y la apertura dejó de ser un archivo regular.
        return None
    # Se comprueba sobre el descriptor ya abierto, no sobre el nombre.
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return None
    huella = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb") as entrada, open(temporal, "wb") as salida:
        while bloque := entrada.read(_BLOQUE):
            huella.update(bloque)
            salida.write(bloque)
            total += len(bloque)
    return huella.hexdigest(), total


def recibir(rutas: Rutas, dia_carga: str, ahora: str) -> ResumenRecepcion:
    """Recibe el buzón de un día. Un contenido ya registrado no se toca."""
    dir_buzon = resolver_confinada(rutas.buzon, dia_carga)
    if not dir_buzon.is_dir():
        return ResumenRecepcion()
    dir_landing = resolver_confinada(rutas.landing, dia_carga)
    dir_landing.mkdir(parents=True, exist_ok=True)

    nuevos = ya_recibidos = no_regulares = 0
    conexion = abrir(rutas.base, "landing")
    try:
        with os.scandir(dir_buzon) as entradas:
            ordenadas = sorted(entradas, key=lambda entrada: entrada.name)
        for numero, entrada in enumerate(ordenadas):
            nombre = sanear_nombre(entrada.name)
            temporal = dir_landing / f".recibiendo-{os.getpid()}-{numero}"
            copiado = None
            if stat.S_ISREG(entrada.stat(follow_symlinks=False).st_mode):
                try:
                    copiado = _copiar_con_huella(entrada.path, temporal)
                except BaseException:
                    temporal.unlink(missing_ok=True)
                    raise
            if copiado is None:
                temporal.unlink(missing_ok=True)
                with transaccion(conexion):
                    cuarentena.registrar(
                        conexion,
                        dia_carga=dia_carga,
                        nombre_archivo=nombre,
                        archivo_id=None,
                        n_fila=0,
                        motivo=_MOTIVO_NO_REGULAR,
                        campos={},
                        fila_cruda=None,
                        ahora=ahora,
                    )
                no_regulares += 1
                continue

            sha256, total = copiado
            existe = conexion.execute(
                "SELECT 1 FROM landing_archivo WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if existe:
                temporal.unlink()
                ya_recibidos += 1
                continue

            extension = _extension(entrada.name)
            relativa = f"{dia_carga}/{sha256}.{extension}"
            destino = resolver_confinada(rutas.landing, relativa)
            os.replace(temporal, destino)
            os.chmod(destino, 0o444)
            with transaccion(conexion):
                conexion.execute(
                    """
                    INSERT INTO landing_archivo
                      (sha256, dia_carga, nombre_original, ruta_relativa, formato, bytes,
                       recibido_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sha256,
                        dia_carga,
                        nombre,
                        relativa,
                        extension if extension in FORMATOS else "otro",
                        total,
                        ahora,
                    ),
                )
            nuevos += 1
    finally:
        conexion.close()
    return ResumenRecepcion(nuevos=nuevos, ya_recibidos=ya_recibidos, no_regulares=no_regulares)


def abrir_verificado(rutas: Rutas, archivo_id: int) -> bytes:
    """Entrega los bytes de un archivo de landing solo si su sha256 coincide."""
    conexion = abrir(rutas.base, "landing")
    try:
        fila = conexion.execute(
            "SELECT ruta_relativa, sha256, bytes FROM landing_archivo WHERE archivo_id = ?",
            (archivo_id,),
        ).fetchone()
    finally:
        conexion.close()
    if fila is None:
        raise LookupError("El archivo no está registrado en landing.")
    relativa, sha256, total = fila
    try:
        ruta = resolver_confinada(rutas.landing, relativa)
        descriptor = os.open(ruta, os.O_RDONLY | _NO_SEGUIR)
        with os.fdopen(descriptor, "rb") as entrada:
            # Un byte de más alcanza para saber que el archivo creció.
            datos = entrada.read(total + 1)
    except (OSError, RutaNoConfinada):
        # Falta, no se puede leer o lo reemplazó un enlace: ya no es lo que llegó.
        raise RechazoArchivo(_MOTIVO_ALTERADO) from None
    if len(datos) != total or hashlib.sha256(datos).hexdigest() != sha256:
        raise RechazoArchivo(_MOTIVO_ALTERADO)
    return datos
