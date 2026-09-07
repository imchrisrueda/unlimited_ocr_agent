import re
from typing import Optional

_RE_BASE64_DATA_URI = re.compile(r"data:image/[a-zA-Z0-9.+_-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_RE_AUTH_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_RE_API_KEY_VAL = re.compile(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]+(['\"]?)", re.IGNORECASE)
_RE_SK_SECRET = re.compile(r"\b(sk-[A-Za-z0-9_-]{6,})\b", re.IGNORECASE)


def sanitize_message(text: Optional[str], max_chars: int = 1000) -> str:
    """Redacta claves API, tokens de autenticación y cadenas base64 de mensajes de error."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _RE_BASE64_DATA_URI.sub("data:image/[REDACTED_BASE64]", text)
    text = _RE_AUTH_BEARER.sub(r"\1[REDACTED_TOKEN]", text)
    text = _RE_API_KEY_VAL.sub(r"\1[REDACTED_API_KEY]\2", text)
    text = _RE_SK_SECRET.sub("[REDACTED_SECRET]", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "... [TRUNCATED]"
    return text


class LMStudioError(Exception):
    """Excepción base para todos los errores del cliente LM Studio."""
    pass


class LMStudioConnectionError(LMStudioError):
    """Error al conectar con el servidor local de LM Studio o timeout de red."""
    pass


class LMStudioModelNotConfiguredError(LMStudioError):
    """Error lanzado cuando no se ha configurado ningún modelo para la operación solicitada."""
    pass


class LMStudioModelNotFoundError(LMStudioError):
    """Error lanzado cuando el modelo configurado no está disponible en el servidor LM Studio."""
    pass


class LMStudioResponseError(LMStudioError):
    """Error devuelto por la API de OpenAI/LM Studio (status HTTP de error o payload inválido)."""
    pass


class LMStudioEmptyResponseError(LMStudioError):
    """Error lanzado cuando el modelo devuelve contenido vacío (p. ej. consumido en razonamiento)."""
    pass


class LMStudioResponseTruncatedError(LMStudioResponseError):
    """Error lanzado cuando la respuesta del modelo es truncada por límite de tokens (finish_reason='length')."""
    def __init__(self, message: str, raw_response: Optional[str] = None):
        sanitized_msg = sanitize_message(message, max_chars=1000)
        super().__init__(sanitized_msg)
        self.raw_response = sanitize_message(raw_response, max_chars=500) if raw_response is not None else None


class StructuredOutputError(LMStudioError):
    """Excepción base para errores de estructuración y parseo de salidas de LM Studio."""
    def __init__(self, message: str, raw_response: Optional[str] = None):
        sanitized_msg = sanitize_message(message, max_chars=1000)
        super().__init__(sanitized_msg)
        self.raw_response = sanitize_message(raw_response, max_chars=500) if raw_response is not None else None


class StructuredOutputParseError(StructuredOutputError):
    """Error cuando la respuesta no es un JSON sintácticamente válido o contiene bloques no conformes."""
    pass


class StructuredOutputValidationError(StructuredOutputError):
    """Error cuando el JSON no cumple con el esquema Pydantic solicitado."""
    pass
