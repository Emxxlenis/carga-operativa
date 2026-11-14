"""Lector de XML. Solo ``defusedxml``: un DTD o una entidad rechazan el archivo.

Forma esperada: ``<carga>`` con hijos ``<registro>``. Cada hijo de la raíz es
una fila; sus atributos y sus elementos hijos son campos. Lo que no encaja se
conserva como campo desconocido para que el contrato lo rechace, no se descarta.
"""

from __future__ import annotations

import logging

from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.ElementTree import ParseError, fromstring

from ..tipos import FilaCruda, RechazoArchivo

_registro = logging.getLogger(__name__)

RAIZ = "carga"
REGISTRO = "registro"


def _poner(campos: dict[str, object], clave: str, valor: str) -> None:
    # Una clave repetida no pisa a la anterior: queda como campo aparte.
    while clave in campos:
        clave = f"{clave} (repetido)"
    campos[clave] = valor


def _texto(valor: str | None) -> str:
    return (valor or "").strip()


def leer(datos: bytes) -> list[FilaCruda]:
    try:
        raiz = fromstring(datos, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden):
        raise RechazoArchivo("xml_con_dtd_o_entidades") from None
    except (ParseError, ValueError, LookupError):
        _registro.warning("XML mal formado.", exc_info=True)
        raise RechazoArchivo("archivo_ilegible", causa="xml_mal_formado") from None
    if raiz.tag != RAIZ:
        raise RechazoArchivo("archivo_ilegible", causa="raiz_inesperada")

    filas: list[FilaCruda] = []
    for n_fila, elemento in enumerate(raiz, start=1):
        campos: dict[str, object] = {}
        if elemento.tag != REGISTRO:
            _poner(campos, "elemento", str(elemento.tag))
        for clave, valor in elemento.attrib.items():
            _poner(campos, clave, _texto(valor))
        if _texto(elemento.text):
            _poner(campos, "texto", _texto(elemento.text))
        for hijo in elemento:
            _poner(campos, str(hijo.tag), _texto(hijo.text))
            for nieto in hijo.iter():
                if nieto is not hijo:
                    _poner(campos, f"{hijo.tag}.{nieto.tag}", _texto(nieto.text))
        filas.append(FilaCruda(n_fila=n_fila, campos=campos))
    return filas
