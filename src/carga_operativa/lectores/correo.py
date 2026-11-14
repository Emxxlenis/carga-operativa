"""Lector de correo exportado (.eml).

El correo es texto. Solo se toma la parte ``text/plain``: no se interpreta
HTML, no se siguen enlaces y no se guardan adjuntos.
"""

from __future__ import annotations

import email
import email.policy
import logging

from ..config import MAX_CUERPO_CORREO
from ..tipos import CorreoTexto, RechazoArchivo

_registro = logging.getLogger(__name__)

MAX_ASUNTO = 200


def leer(datos: bytes) -> CorreoTexto:
    mensaje = email.message_from_bytes(datos, policy=email.policy.default)
    try:
        parte = mensaje.get_body(preferencelist=("plain",))
        if parte is None or parte.get_content_type() != "text/plain":
            raise RechazoArchivo("correo_sin_texto_plano")
        cuerpo = parte.get_content()
        asunto = str(mensaje.get("subject", ""))
    except (LookupError, UnicodeError, ValueError):
        _registro.warning("Correo que no se pudo decodificar.", exc_info=True)
        raise RechazoArchivo("archivo_ilegible", causa="correo_invalido") from None
    if not isinstance(cuerpo, str):
        raise RechazoArchivo("correo_sin_texto_plano")
    return CorreoTexto(asunto=asunto[:MAX_ASUNTO], cuerpo=cuerpo[:MAX_CUERPO_CORREO])
