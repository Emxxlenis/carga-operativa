"""CLI: ``python -m carga_operativa generar | dia AAAA-MM-DD | demo``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import pipeline
from .config import ConfiguracionInvalida, Rutas

CODIGO_USO = 64


def _configurar_registro(rutas: Rutas) -> None:
    """Las trazas técnicas van a un archivo local, nunca a la base ni a la demo."""
    rutas.raiz.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=rutas.registro,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _linea(resultado: pipeline.ResultadoDia) -> str:
    leidas = sum(archivo.filas_leidas for archivo in resultado.archivos)
    aceptadas = sum(archivo.aceptadas for archivo in resultado.archivos)
    rechazadas = sum(archivo.rechazadas for archivo in resultado.archivos)
    archivos_rechazados = sum(1 for archivo in resultado.archivos if archivo.estado == "rechazado")
    texto = (
        f"{resultado.dia} {resultado.estado}: "
        f"{resultado.recepcion.nuevos} archivos nuevos, "
        f"{resultado.recepcion.ya_recibidos} ya recibidos, "
        f"{leidas} filas leídas, {aceptadas} aceptadas, {rechazadas} en cuarentena, "
        f"{archivos_rechazados} archivos rechazados, "
        f"{resultado.filas_pronostico} filas de línea base"
    )
    if not resultado.cuadra:
        texto += f". No cuadra: {', '.join(resultado.controles_fallidos)}"
    return texto


def _analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="carga_operativa",
        description="Carga operativa: caso de demostración con datos sintéticos.",
    )
    analizador.add_argument(
        "--datos", type=Path, default=Path("datos"), help="Directorio de trabajo (no versionado)."
    )
    comandos = analizador.add_subparsers(dest="comando", required=True)
    comandos.add_parser("generar", help="Escribe el buzón sintético con la semilla fija.")
    un_dia = comandos.add_parser("dia", help="Procesa un día de carga.")
    un_dia.add_argument("dia", help="Día de carga, AAAA-MM-DD.")
    demo = comandos.add_parser("demo", help="Genera los datos, corre cada día y exporta docs/.")
    demo.add_argument(
        "--salida", type=Path, default=Path("docs"), help="Dónde escribir la página estática."
    )
    return analizador


def main(argumentos: Sequence[str] | None = None) -> int:
    opciones = _analizador().parse_args(argumentos)
    rutas = Rutas(raiz=opciones.datos)

    try:
        if opciones.comando == "dia":
            # El día se valida antes de crear el directorio de trabajo.
            dia = pipeline.validar_dia(opciones.dia)
            _configurar_registro(rutas)
            resultado = pipeline.dia(rutas, dia)
            print(_linea(resultado))
            return resultado.codigo_salida

        _configurar_registro(rutas)
        if opciones.comando == "generar":
            dias = pipeline.generar_sintetico(rutas)
            print(f"Buzón sintético generado: {len(dias)} días, de {dias[0]} a {dias[-1]}.")
            return pipeline.CODIGO_OK

        resultados = pipeline.demo(rutas, opciones.salida)
        for resultado in resultados:
            print(_linea(resultado))
        print("Demo exportada.")
        return max(resultado.codigo_salida for resultado in resultados)
    except (pipeline.DiaInvalido, ConfiguracionInvalida) as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_USO


if __name__ == "__main__":
    raise SystemExit(main())
