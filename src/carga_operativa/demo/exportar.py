"""Exporta la demo: una página estática, sin JavaScript.

Lee con conexión de solo lectura lo que ya está en mart, conciliación,
pronóstico y cuarentena. Cada valor pasa por ``html.escape`` antes de entrar a
la plantilla. Los mismos datos quedan en ``datos/tablero.json``.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from .. import cuarentena
from ..contrato import motivos
from ..db.conexion import ConexionCapa, abrir

DIAS_SERIE = 14
_CADA_CUANTAS_ETIQUETAS = 3  # la última barra (el día cargado) siempre lleva fecha

AVISO = (
    "No es una granja real ni datos de un empleador. Todo lo que se ve aquí lo generó el "
    "propio repositorio con la semilla {semilla}, incluidos los defectos."
)
ROTULO_LINEA_BASE = (
    "Línea base (promedio o estacional simple). No es un modelo predictivo validado."
)

_ARCHIVOS_ESTATICOS = ("estilos.css",)

# (métrica, título, unidad, tabla del mart, columna de medida, columna de conteo).
# Identificadores fijos, no datos.
_METRICAS = (
    ("produccion", "Producción", "kg", "mart_produccion", "produccion_kg", "lotes_con_produccion"),
    (
        "mortalidad",
        "Mortalidad",
        "aves",
        "mart_mortalidad",
        "mortalidad_aves",
        "lotes_con_mortalidad",
    ),
    ("ventas", "Ventas", "kg", "mart_ventas", "ventas_kg", "lotes_con_ventas"),
)
_DECIMALES = {"produccion": 1, "mortalidad": 0, "ventas": 1}

_CONTROLES = (
    ("archivos", "Archivos recibidos frente a archivos procesados por el contrato"),
    ("filas", "Filas leídas frente a filas aceptadas más filas en cuarentena"),
    ("filas_por_archivo", "Archivos que no cuadran por sí solos (se espera cero)"),
    ("grano_modelo", "Filas aceptadas frente a medidas cargadas en el modelo"),
    ("totales_modelo", "Métricas cuyo total difiere entre contrato y modelo (se espera cero)"),
    ("totales_mart", "Métricas cuyo total difiere entre modelo y tablero (se espera cero)"),
)

_METODOS = {
    "promedio_movil_7": "promedio móvil de 7 días",
    "estacional_semanal": "estacional semanal simple (mismo día de la semana, hasta 4 semanas)",
}
_EXTRACTORES = {
    "determinista": "determinista de demostración (sin clave de API)",
    "api": "servicio externo opcional",
}
_ESTADOS = {
    "cuadra": (
        "cuadra",
        "estado-cuadra",
        "Todo lo que entró está contado: aceptado o en cuarentena.",
    ),
    "no_cuadra": (
        "no cuadra",
        "estado-no-cuadra",
        "Algún control no cuadra. El pronóstico no corre hasta que cuadre.",
    ),
    "pendiente": (
        "pendiente",
        "estado-no-cuadra",
        "El día no terminó de procesarse. El pronóstico no corre.",
    ),
}
_SIN_ESTADO = ("sin calcular", "estado-no-cuadra", "El día no tiene conciliación calculada.")

_DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _e(valor: object) -> str:
    return html.escape(str(valor), quote=True)


def _numero(valor: float | None, decimales: int = 0) -> str:
    """Número con separadores en español: 1.234,5."""
    if valor is None:
        return "sin dato"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", " ").replace(".", ",").replace(" ", ".")


def _fecha_larga(fecha: dt.date) -> str:
    return (
        f"{_DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {_MESES[fecha.month - 1]} de {fecha.year}"
    )


def _fecha_corta(fecha: dt.date) -> str:
    return f"{fecha.day:02d}/{fecha.month:02d}"


# --- Datos -------------------------------------------------------------------


def _conciliacion(conexion: ConexionCapa, dia: str) -> dict[str, Any]:
    fila = conexion.execute(
        "SELECT estado FROM conciliacion_dia WHERE dia_carga = ?", (dia,)
    ).fetchone()
    guardados = {
        control: (esperado, obtenido, bool(cuadra))
        for control, esperado, obtenido, cuadra in conexion.execute(
            "SELECT control, esperado, obtenido, cuadra FROM conciliacion_control "
            "WHERE dia_carga = ?",
            (dia,),
        )
    }
    controles = [
        {
            "control": control,
            "descripcion": descripcion,
            "esperado": guardados[control][0],
            "obtenido": guardados[control][1],
            "cuadra": guardados[control][2],
        }
        for control, descripcion in _CONTROLES
        if control in guardados
    ]
    return {"estado": fila[0] if fila else None, "controles": controles}


def _paneles(conexion: ConexionCapa, dia: dt.date) -> list[dict[str, Any]]:
    fechas = [dia - dt.timedelta(days=resta) for resta in range(DIAS_SERIE - 1, -1, -1)]
    paneles: list[dict[str, Any]] = []
    for metrica, titulo, unidad, tabla, medida, conteo in _METRICAS:
        resumen = conexion.execute(
            f"SELECT {medida}, {conteo} FROM mart_resumen_dia WHERE fecha = ?",
            (dia.isoformat(),),
        ).fetchone()
        por_lote = [
            {"lote": lote, "galpon": galpon, "valor": valor}
            for lote, galpon, valor in conexion.execute(
                f"SELECT lote, galpon, {medida} FROM {tabla} WHERE fecha = ? ORDER BY lote",
                (dia.isoformat(),),
            )
        ]
        conciliados = dict(
            conexion.execute(
                f"""
                SELECT m.fecha, m.{medida}
                FROM mart_resumen_dia AS m
                JOIN conciliacion_dia AS c ON c.dia_carga = m.fecha
                WHERE c.estado = 'cuadra' AND m.fecha BETWEEN ? AND ?
                """,
                (fechas[0].isoformat(), fechas[-1].isoformat()),
            )
        )
        paneles.append(
            {
                "metrica": metrica,
                "titulo": titulo,
                "unidad": unidad,
                "total": resumen[0] if resumen else None,
                "lotes_con_dato": resumen[1] if resumen else 0,
                "por_lote": por_lote,
                "serie": [
                    {"fecha": fecha.isoformat(), "valor": conciliados.get(fecha.isoformat())}
                    for fecha in fechas
                ],
            }
        )
    return paneles


def _linea_base(conexion: ConexionCapa, dia: str, estado: str | None) -> dict[str, Any]:
    filas = conexion.execute(
        """
        SELECT fecha_objetivo, metrica, valor, metodo, n_observaciones, mae_retro
        FROM pronostico_linea_base
        WHERE dia_base = ?
        ORDER BY fecha_objetivo, metrica
        """,
        (dia,),
    ).fetchall()
    puntos = [
        {
            "fecha_objetivo": fecha,
            "metrica": metrica,
            "valor": valor,
            "metodo": metodo,
            "n_observaciones": observaciones,
            "mae_retro": mae,
        }
        for fecha, metrica, valor, metodo, observaciones, mae in filas
    ]
    sin_emitir = None
    if not puntos:
        if estado != "cuadra":
            sin_emitir = "No se emitió: la conciliación del día no cuadra."
        else:
            sin_emitir = "No se emitió: hay menos de 3 días conciliados de historia."
    return {"rotulo": ROTULO_LINEA_BASE, "puntos": puntos, "sin_emitir": sin_emitir}


def _no_entro(conexion: ConexionCapa, dia: str) -> list[dict[str, Any]]:
    grupos: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rechazo in cuarentena.listar(conexion, dia):
        clave = (rechazo.nombre_archivo, rechazo.nivel, rechazo.motivo)
        grupo = grupos.setdefault(
            clave,
            {
                "archivo": rechazo.nombre_archivo,
                "nivel": rechazo.nivel,
                "cantidad": 0,
                "motivo": rechazo.motivo,
                "explicacion": motivos.explicar(rechazo.motivo),
                "detalle": rechazo.detalle,
            },
        )
        grupo["cantidad"] = int(grupo["cantidad"]) + 1
    for grupo in grupos.values():
        if grupo["nivel"] == cuarentena.NIVEL_ARCHIVO:
            grupo["alcance"] = "Archivo completo"
        else:
            cantidad = grupo["cantidad"]
            grupo["alcance"] = "1 fila" if cantidad == 1 else f"{cantidad} filas"
    return sorted(
        grupos.values(), key=lambda g: (g["nivel"] != "archivo", g["archivo"], g["motivo"])
    )


def reunir(
    conexion: ConexionCapa, dia: str, *, extractor: str, version_contrato: str, semilla: int
) -> dict[str, Any]:
    """Todo lo que muestra la página, como un diccionario serializable."""
    conciliacion = _conciliacion(conexion, dia)
    estado = conciliacion["estado"]
    return {
        "aviso": "Datos de demostración. " + AVISO.format(semilla=semilla),
        "semilla": semilla,
        "dia": dia,
        "conciliacion": conciliacion,
        "paneles": _paneles(conexion, dt.date.fromisoformat(dia)),
        "linea_base": _linea_base(conexion, dia, estado if isinstance(estado, str) else None),
        "no_entro": _no_entro(conexion, dia),
        "pie": {"extractor": extractor, "version_contrato": version_contrato, "semilla": semilla},
    }


# --- HTML --------------------------------------------------------------------


def _html_controles(controles: list[dict[str, Any]]) -> str:
    if not controles:
        return '        <tr><td colspan="5">Sin controles calculados.</td></tr>'
    filas = []
    for control in controles:
        clase, texto = ("bien", "Cuadra") if control["cuadra"] else ("mal", "No cuadra")
        filas.append(
            "        <tr>"
            f'<th scope="row" class="codigo">{_e(control["control"])}</th>'
            f"<td>{_e(control['descripcion'])}</td>"
            f'<td class="num">{_e(_numero(control["esperado"]))}</td>'
            f'<td class="num">{_e(_numero(control["obtenido"]))}</td>'
            f'<td class="{clase}">{texto}</td>'
            "</tr>"
        )
    return "\n".join(filas)


def _svg_serie(panel: dict[str, Any], dia: str) -> str:
    serie: list[dict[str, Any]] = panel["serie"]
    decimales = _DECIMALES[str(panel["metrica"])]
    ancho, alto = 300.0, 132.0
    izquierda, arriba, abajo = 4.0, 16.0, 20.0
    paso = (ancho - 2 * izquierda) / len(serie)
    ancho_barra = paso * 0.68
    base = alto - abajo
    valores = [float(p["valor"]) for p in serie if p["valor"] is not None]
    maximo = max(valores, default=0.0) or 1.0
    descripcion = (
        f"{panel['titulo']} total por día, últimos {len(serie)} días, en {panel['unidad']}. "
        f"Máximo {_numero(max(valores, default=0.0), decimales)}."
    )
    partes = [
        f'<svg class="grafico" viewBox="0 0 {ancho:.0f} {alto:.0f}" role="img" '
        f'aria-label="{_e(descripcion)}">',
        f'<line class="eje" x1="{izquierda:.1f}" y1="{base:.1f}" '
        f'x2="{ancho - izquierda:.1f}" y2="{base:.1f}"></line>',
        f'<text class="etiqueta" x="{izquierda:.1f}" y="10">'
        f"máx. {_e(_numero(max(valores, default=0.0), decimales))} {_e(panel['unidad'])}</text>",
    ]
    for indice, punto in enumerate(serie):
        fecha = dt.date.fromisoformat(str(punto["fecha"]))
        x = izquierda + indice * paso + (paso - ancho_barra) / 2
        centro = x + ancho_barra / 2
        if punto["valor"] is not None:
            valor = float(punto["valor"])
            altura = (base - arriba) * valor / maximo
            clase = "barra barra-dia" if punto["fecha"] == dia else "barra"
            partes.append(
                f'<rect class="{clase}" x="{x:.1f}" y="{base - altura:.1f}" '
                f'width="{ancho_barra:.1f}" height="{altura:.1f}">'
                f"<title>{_e(punto['fecha'])}: {_e(_numero(valor, decimales))} "
                f"{_e(panel['unidad'])}</title></rect>"
            )
        if (len(serie) - 1 - indice) % _CADA_CUANTAS_ETIQUETAS == 0:
            partes.append(
                f'<text class="etiqueta" x="{centro:.1f}" y="{alto - 6:.1f}" '
                f'text-anchor="middle">{_e(_fecha_corta(fecha))}</text>'
            )
    partes.append("</svg>")
    return "".join(partes)


def _html_panel(panel: dict[str, Any], dia: str) -> str:
    decimales = _DECIMALES[str(panel["metrica"])]
    unidad = _e(panel["unidad"])
    por_lote: list[dict[str, Any]] = panel["por_lote"]
    lotes = panel["lotes_con_dato"]
    detalle = "1 lote con dato" if lotes == 1 else f"{lotes} lotes con dato"
    if por_lote:
        filas = "\n".join(
            "          <tr>"
            f'<th scope="row">{_e(fila["lote"])}</th>'
            f"<td>{_e(fila['galpon'])}</td>"
            f'<td class="num">{_e(_numero(fila["valor"], decimales))}</td>'
            "</tr>"
            for fila in por_lote
        )
    else:
        filas = '          <tr><td colspan="3">Ningún lote con dato aceptado.</td></tr>'
    return f"""      <article class="panel">
        <h3>{_e(panel["titulo"])}</h3>
        <p class="panel-total">{_e(_numero(panel["total"], decimales))} <span class="panel-unidad">{unidad}</span></p>
        <p class="panel-detalle">Total del día. {_e(detalle)}.</p>
        <p class="grafico-titulo">Total por día, últimos {DIAS_SERIE} días conciliados</p>
        {_svg_serie(panel, dia)}
        <table>
          <caption>Detalle por lote</caption>
          <thead><tr><th scope="col">Lote</th><th scope="col">Galpón</th><th scope="col" class="num">{_e(panel["titulo"])} ({unidad})</th></tr></thead>
          <tbody>
{filas}
          </tbody>
        </table>
      </article>"""


def _html_linea_base(linea_base: dict[str, Any]) -> str:
    if linea_base["sin_emitir"]:
        return f'    <p class="nota">{_e(linea_base["sin_emitir"])}</p>'
    puntos: list[dict[str, Any]] = linea_base["puntos"]
    por_fecha: dict[str, dict[str, dict[str, Any]]] = {}
    por_metrica: dict[str, dict[str, Any]] = {}
    for punto in puntos:
        por_fecha.setdefault(str(punto["fecha_objetivo"]), {})[str(punto["metrica"])] = punto
        por_metrica.setdefault(str(punto["metrica"]), punto)

    encabezados = "".join(
        f'<th scope="col" class="num">{_e(titulo)} ({_e(unidad)})</th>'
        for _metrica, titulo, unidad, *_resto in _METRICAS
    )
    filas = []
    for fecha_iso, metricas in sorted(por_fecha.items()):
        fecha = dt.date.fromisoformat(fecha_iso)
        celdas = "".join(
            '<td class="num">'
            + _e(
                _numero(float(metricas[metrica]["valor"]), 1)
                if metrica in metricas
                else "sin línea base"
            )
            + "</td>"
            for metrica, *_resto in _METRICAS
        )
        filas.append(f'        <tr><th scope="row">{_e(_fecha_larga(fecha))}</th>{celdas}</tr>')

    notas = []
    for metrica, titulo, unidad, *_resto in _METRICAS:
        punto = por_metrica.get(metrica)
        if punto is None:
            notas.append(
                f"      <li>{_e(titulo)}: sin línea base, hay menos de 3 días conciliados "
                "con dato.</li>"
            )
            continue
        error = (
            f"error absoluto medio retrospectivo de {_numero(float(punto['mae_retro']), 1)} "
            f"{unidad}"
            if punto["mae_retro"] is not None
            else "sin error retrospectivo calculable"
        )
        notas.append(
            f"      <li>{_e(titulo)}: {_e(_METODOS[str(punto['metodo'])])}, "
            f"{_e(punto['n_observaciones'])} días observados, {_e(error)}.</li>"
        )
    return f"""    <div class="tabla-ancha">
    <table>
      <caption>Total esperado por día si se repite el patrón reciente</caption>
      <thead><tr><th scope="col">Fecha objetivo</th>{encabezados}</tr></thead>
      <tbody>
{chr(10).join(filas)}
      </tbody>
    </table>
    </div>
    <ul class="lista">
{chr(10).join(notas)}
    </ul>"""


def _html_no_entro(no_entro: list[dict[str, Any]]) -> str:
    if not no_entro:
        return ""
    filas = "\n".join(
        "        <tr>"
        f'<th scope="row">{_e(grupo["archivo"])}</th>'
        f"<td>{_e(grupo['alcance'])}</td>"
        f"<td>{_e(grupo['explicacion'])}</td>"
        f"<td>{_e(grupo['detalle'])}</td>"
        "</tr>"
        for grupo in no_entro
    )
    return f"""    <div class="tabla-ancha">
    <table>
      <caption>Rechazos del día, agrupados por archivo y motivo</caption>
      <thead><tr><th scope="col">Archivo</th><th scope="col">Alcance</th><th scope="col">Motivo</th><th scope="col">Detalle de ejemplo</th></tr></thead>
      <tbody>
{filas}
      </tbody>
    </table>
    </div>"""


def _nota_no_entro(no_entro: list[dict[str, Any]]) -> str:
    if not no_entro:
        return "Nada quedó en cuarentena este día."
    archivos = sum(1 for grupo in no_entro if grupo["nivel"] == "archivo")
    filas = sum(int(grupo["cantidad"]) for grupo in no_entro if grupo["nivel"] == "fila")
    return (
        f"En cuarentena: {archivos} archivo(s) completo(s) y {filas} fila(s). "
        "Nada de esto llegó al tablero, y nada se corrigió ni se convirtió para que entrara."
    )


def renderizar(datos: dict[str, Any]) -> str:
    """La página completa. Todo texto variable entra escapado."""
    plantilla = Template(
        resources.files(__package__).joinpath("plantilla.html").read_text(encoding="utf-8")
    )
    dia = str(datos["dia"])
    conciliacion: dict[str, Any] = datos["conciliacion"]
    estado_texto, estado_clase, estado_nota = _ESTADOS.get(str(conciliacion["estado"]), _SIN_ESTADO)
    pie: dict[str, Any] = datos["pie"]
    paneles: list[dict[str, Any]] = datos["paneles"]
    no_entro: list[dict[str, Any]] = datos["no_entro"]
    return plantilla.substitute(
        aviso=_e(AVISO.format(semilla=datos["semilla"])),
        semilla=_e(datos["semilla"]),
        dia=_e(dia),
        dia_largo=_e(_fecha_larga(dt.date.fromisoformat(dia))),
        estado_clase=estado_clase,
        estado_texto=_e(estado_texto),
        estado_nota=_e(estado_nota),
        controles=_html_controles(conciliacion["controles"]),
        paneles="\n".join(_html_panel(panel, dia) for panel in paneles),
        linea_base=_html_linea_base(datos["linea_base"]),
        no_entro_nota=_e(_nota_no_entro(no_entro)),
        no_entro=_html_no_entro(no_entro),
        extractor=_e(_EXTRACTORES.get(str(pie["extractor"]), str(pie["extractor"]))),
        version_contrato=_e(pie["version_contrato"]),
    )


def _json_seguro(datos: dict[str, Any]) -> str:
    """JSON con los signos de marcado escapados, por si alguien lo incrusta en HTML."""
    texto = json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True)
    return texto.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e") + "\n"


def exportar(
    ruta_base: Path,
    dia: str,
    destino: Path,
    *,
    extractor: str,
    version_contrato: str,
    semilla: int,
) -> dict[str, Any]:
    """Escribe ``index.html``, ``estilos.css`` y ``datos/tablero.json`` en ``destino``."""
    conexion = abrir(ruta_base, "demo")
    try:
        datos = reunir(
            conexion, dia, extractor=extractor, version_contrato=version_contrato, semilla=semilla
        )
    finally:
        conexion.close()

    destino = Path(destino)
    (destino / "datos").mkdir(parents=True, exist_ok=True)
    (destino / "index.html").write_text(renderizar(datos), encoding="utf-8")
    (destino / "datos" / "tablero.json").write_text(_json_seguro(datos), encoding="utf-8")
    for nombre in _ARCHIVOS_ESTATICOS:
        contenido = resources.files(__package__).joinpath(nombre).read_text(encoding="utf-8")
        (destino / nombre).write_text(contenido, encoding="utf-8")
    return datos
