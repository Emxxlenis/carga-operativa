"""Lista cerrada de motivos de cuarentena y redacción del detalle.

El detalle sale siempre de una plantilla fija. Nunca entra aquí el texto de
una excepción ni una traza.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from enum import StrEnum

from ..tipos import Formula

MAX_TEXTO = 80
MAX_CLAVES_FILA = 20


class Motivo(StrEnum):
    # Nivel fila, en el orden en que se evalúan.
    CAMPO_EXTRA = "campo_extra"
    COLUMNA_FALTANTE = "columna_faltante"
    FORMULA_NO_EVALUADA = "formula_no_evaluada"
    TIPO_INVALIDO = "tipo_invalido"
    FECHA_INVALIDA = "fecha_invalida"
    FECHA_FUERA_DE_DIA = "fecha_fuera_de_dia"
    METRICA_DESCONOCIDA = "metrica_desconocida"
    UNIDAD_DISTINTA = "unidad_distinta"
    LOTE_DESCONOCIDO = "lote_desconocido"
    VALOR_FUERA_DE_RANGO = "valor_fuera_de_rango"
    GRANO_DUPLICADO = "grano_duplicado"
    # Nivel archivo.
    ENTRADA_NO_REGULAR = "entrada_no_regular"
    ARCHIVO_ALTERADO = "archivo_alterado"
    ARCHIVO_DEMASIADO_GRANDE = "archivo_demasiado_grande"
    EXTENSION_NO_ADMITIDA = "extension_no_admitida"
    EXCEL_CON_MACROS = "excel_con_macros"
    XML_CON_DTD_O_ENTIDADES = "xml_con_dtd_o_entidades"
    CORREO_SIN_TEXTO_PLANO = "correo_sin_texto_plano"
    EXTRACCION_INVALIDA = "extraccion_invalida"
    ARCHIVO_ILEGIBLE = "archivo_ilegible"


MOTIVOS_FILA: tuple[Motivo, ...] = (
    Motivo.CAMPO_EXTRA,
    Motivo.COLUMNA_FALTANTE,
    Motivo.FORMULA_NO_EVALUADA,
    Motivo.TIPO_INVALIDO,
    Motivo.FECHA_INVALIDA,
    Motivo.FECHA_FUERA_DE_DIA,
    Motivo.METRICA_DESCONOCIDA,
    Motivo.UNIDAD_DISTINTA,
    Motivo.LOTE_DESCONOCIDO,
    Motivo.VALOR_FUERA_DE_RANGO,
    Motivo.GRANO_DUPLICADO,
)

MOTIVOS_ARCHIVO: tuple[Motivo, ...] = (
    Motivo.ENTRADA_NO_REGULAR,
    Motivo.ARCHIVO_ALTERADO,
    Motivo.ARCHIVO_DEMASIADO_GRANDE,
    Motivo.EXTENSION_NO_ADMITIDA,
    Motivo.EXCEL_CON_MACROS,
    Motivo.XML_CON_DTD_O_ENTIDADES,
    Motivo.CORREO_SIN_TEXTO_PLANO,
    Motivo.EXTRACCION_INVALIDA,
    Motivo.ARCHIVO_ILEGIBLE,
)

_PLANTILLAS: dict[Motivo, str] = {
    Motivo.CAMPO_EXTRA: "La fila trae campos que el contrato no define: {campos}.",
    Motivo.COLUMNA_FALTANTE: (
        "Falta o viene vacío un campo obligatorio: {campos}. No se completa por suposición."
    ),
    Motivo.FORMULA_NO_EVALUADA: (
        "El campo {campo} trae una fórmula de Excel ({formula}). No se evalúa."
    ),
    Motivo.TIPO_INVALIDO: "El campo {campo} tiene un valor que el contrato no admite: {valor}.",
    Motivo.FECHA_INVALIDA: (
        "La fecha {valor} no es una fecha de calendario con formato AAAA-MM-DD."
    ),
    Motivo.FECHA_FUERA_DE_DIA: "La fecha {valor} no corresponde al día de carga {dia}.",
    Motivo.METRICA_DESCONOCIDA: "La métrica {valor} no es produccion, mortalidad ni ventas.",
    Motivo.UNIDAD_DISTINTA: (
        "La unidad recibida es {recibida} y el contrato espera {esperada} para {metrica}. "
        "No se convierte."
    ),
    Motivo.LOTE_DESCONOCIDO: "El lote {valor} no está en el catálogo de lotes.",
    Motivo.VALOR_FUERA_DE_RANGO: (
        "El valor {valor} está fuera del rango admitido para {metrica} (de 0 a {maximo})."
    ),
    Motivo.GRANO_DUPLICADO: (
        "Ya hay un valor aceptado para {fecha}, lote {lote}, {metrica}. "
        "El dato aceptado no se reemplaza."
    ),
    Motivo.ENTRADA_NO_REGULAR: (
        "La entrada del buzón no es un archivo regular (enlace simbólico, directorio o "
        "dispositivo). No se copió ni se siguió."
    ),
    Motivo.ARCHIVO_ALTERADO: (
        "El contenido guardado ya no coincide con la huella registrada al recibirlo."
    ),
    Motivo.ARCHIVO_DEMASIADO_GRANDE: "El archivo supera el tamaño máximo admitido.",
    Motivo.EXTENSION_NO_ADMITIDA: (
        "La extensión {extension} no se admite. Solo se leen xlsx, xml y eml."
    ),
    Motivo.EXCEL_CON_MACROS: "El libro de Excel contiene macros. No se abre.",
    Motivo.XML_CON_DTD_O_ENTIDADES: (
        "El XML declara un DTD o entidades. No se resuelven y el archivo no se lee."
    ),
    Motivo.CORREO_SIN_TEXTO_PLANO: (
        "El correo no trae una parte de texto plano. No se interpreta HTML ni adjuntos."
    ),
    Motivo.EXTRACCION_INVALIDA: (
        "La salida del extractor no es una lista de registros con claves de texto."
    ),
    Motivo.ARCHIVO_ILEGIBLE: "El archivo no se pudo leer: {causa}.",
}

_EXPLICACIONES: dict[Motivo, str] = {
    Motivo.CAMPO_EXTRA: "Campo que el contrato no define",
    Motivo.COLUMNA_FALTANTE: "Falta una columna obligatoria",
    Motivo.FORMULA_NO_EVALUADA: "Fórmula de Excel sin evaluar",
    Motivo.TIPO_INVALIDO: "Tipo de dato no admitido",
    Motivo.FECHA_INVALIDA: "Fecha imposible o con otro formato",
    Motivo.FECHA_FUERA_DE_DIA: "Fecha de otro día",
    Motivo.METRICA_DESCONOCIDA: "Métrica desconocida",
    Motivo.UNIDAD_DISTINTA: "Unidad distinta a la del contrato",
    Motivo.LOTE_DESCONOCIDO: "Lote que no está en el catálogo",
    Motivo.VALOR_FUERA_DE_RANGO: "Valor fuera de rango",
    Motivo.GRANO_DUPLICADO: "Dato repetido para el mismo día y lote",
    Motivo.ENTRADA_NO_REGULAR: "No es un archivo regular",
    Motivo.ARCHIVO_ALTERADO: "Archivo alterado después de recibirlo",
    Motivo.ARCHIVO_DEMASIADO_GRANDE: "Archivo demasiado grande",
    Motivo.EXTENSION_NO_ADMITIDA: "Extensión no admitida",
    Motivo.EXCEL_CON_MACROS: "Excel con macros",
    Motivo.XML_CON_DTD_O_ENTIDADES: "XML con DTD o entidades",
    Motivo.CORREO_SIN_TEXTO_PLANO: "Correo sin texto plano",
    Motivo.EXTRACCION_INVALIDA: "Salida del extractor con forma inválida",
    Motivo.ARCHIVO_ILEGIBLE: "Archivo ilegible",
}

# Causas de 'archivo_ilegible'. Es una lista cerrada de frases propias: el
# lector elige un código y aquí se traduce; nada viene de la librería.
_CAUSAS_ILEGIBLE: dict[str, str] = {
    "zip_invalido": "no es un libro de Excel válido",
    "excel_invalido": "la estructura del libro de Excel no se reconoce",
    "xml_interno_con_dtd": "el libro de Excel trae XML con DTD o entidades",
    "varias_hojas": "el libro debe tener una sola hoja",
    "encabezado_repetido": "hay un encabezado repetido",
    "xml_mal_formado": "el XML está mal formado",
    "raiz_inesperada": "el elemento raíz del XML no es el esperado",
    "correo_invalido": "el correo no se pudo decodificar como texto",
    "sin_filas": "no contiene filas",
}
_CAUSA_GENERICA = "la estructura no se reconoce"
_SIN_DATO = "(sin dato)"
# Un entero más largo no se convierte a texto: Python limita esa conversión y
# la haría fallar con una excepción.
_ENTERO_MAXIMO = 10**30
_ENTERO_ENORME = "(entero demasiado grande)"


def es_motivo(valor: str) -> bool:
    return valor in Motivo._value2member_map_


def es_de_archivo(motivo: str) -> bool:
    return Motivo(motivo) in MOTIVOS_ARCHIVO


def explicar(motivo: str) -> str:
    """Motivo en lenguaje llano, para la demo."""
    return _EXPLICACIONES[Motivo(motivo)]


def _sin_control(texto: str) -> str:
    return "".join(caracter if caracter.isprintable() else " " for caracter in texto)


def _recortar(texto: str, maximo: int = MAX_TEXTO) -> str:
    limpio = _sin_control(texto).strip()
    if len(limpio) > maximo:
        return limpio[:maximo] + "..."
    return limpio


def limpiar(valor: object) -> str:
    """Texto seguro para un detalle: sin caracteres de control y truncado.

    Solo convierte tipos conocidos. De cualquier otro objeto se informa el
    nombre del tipo, sin invocar su ``__str__``.
    """
    if valor is None:
        return "(vacío)"
    if isinstance(valor, bool):
        return "verdadero" if valor else "falso"
    if isinstance(valor, int):
        return _recortar(repr(valor)) if abs(valor) <= _ENTERO_MAXIMO else _ENTERO_ENORME
    if isinstance(valor, float):
        return _recortar(repr(valor))
    if isinstance(valor, str):
        return _recortar(valor) or "(vacío)"
    if isinstance(valor, Formula):
        return _recortar(valor.texto)
    if isinstance(valor, dt.date):
        return valor.isoformat()
    if isinstance(valor, list | tuple):
        return _recortar(", ".join(limpiar(elemento) for elemento in valor))
    return f"(valor de tipo {type(valor).__name__})"


class _Campos(dict[str, str]):
    def __missing__(self, clave: str) -> str:
        return _SIN_DATO


def redactar(motivo: str, **campos: object) -> str:
    """Arma el detalle de un rechazo desde la plantilla fija del motivo."""
    codigo = Motivo(motivo)
    valores = _Campos({clave: limpiar(valor) for clave, valor in campos.items()})
    if codigo is Motivo.ARCHIVO_ILEGIBLE:
        causa = campos.get("causa")
        frase = _CAUSAS_ILEGIBLE.get(causa) if isinstance(causa, str) else None
        valores["causa"] = frase or _CAUSA_GENERICA
    return _PLANTILLAS[codigo].format_map(valores)


def _valor_seguro(valor: object) -> object:
    if valor is None or isinstance(valor, bool):
        return valor
    if isinstance(valor, int):
        return valor if abs(valor) <= _ENTERO_MAXIMO else _ENTERO_ENORME
    if isinstance(valor, float):
        return valor if valor == valor and abs(valor) != float("inf") else repr(valor)
    if isinstance(valor, Formula):
        return {"formula": _recortar(valor.texto)}
    return limpiar(valor)


def fila_segura(campos: Mapping[object, object]) -> dict[str, object]:
    """Copia acotada de una fila cruda, lista para guardar como JSON."""
    segura: dict[str, object] = {}
    for indice, (clave, valor) in enumerate(campos.items()):
        if indice >= MAX_CLAVES_FILA:
            segura["(más campos)"] = len(campos) - MAX_CLAVES_FILA
            break
        segura[limpiar(clave)] = _valor_seguro(valor)
    return segura
