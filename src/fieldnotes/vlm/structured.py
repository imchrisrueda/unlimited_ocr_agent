import json
import re
import copy
from typing import TypeVar, Type, Any
from pydantic import BaseModel, ValidationError
from .errors import (
    StructuredOutputError,
    StructuredOutputParseError,
    StructuredOutputValidationError,
    sanitize_message,
)

T = TypeVar("T", bound=BaseModel)

_RE_SINGLE_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def _ensure_additional_properties_false(schema_dict: dict[str, Any]) -> None:
    """Recursivamente establece additionalProperties: false en esquemas de tipo object."""
    if not isinstance(schema_dict, dict):
        return

    if schema_dict.get("type") == "object":
        schema_dict["additionalProperties"] = False

    # Procesar propiedades
    for prop in schema_dict.get("properties", {}).values():
        if isinstance(prop, dict):
            _ensure_additional_properties_false(prop)

    # Procesar $defs / definitions
    for d in schema_dict.get("$defs", {}).values():
        if isinstance(d, dict):
            _ensure_additional_properties_false(d)
    for d in schema_dict.get("definitions", {}).values():
        if isinstance(d, dict):
            _ensure_additional_properties_false(d)

    # Procesar items en arrays
    items = schema_dict.get("items")
    if isinstance(items, dict):
        _ensure_additional_properties_false(items)

    # Procesar allOf, anyOf, oneOf
    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in schema_dict.get(combiner, []):
            if isinstance(sub, dict):
                _ensure_additional_properties_false(sub)


def generate_json_schema(schema_class: Type[BaseModel]) -> dict[str, Any]:
    """Genera el payload response_format de OpenAI/LM Studio con modo estricto determinista.

    No muta la clase Pydantic de entrada y asegura additionalProperties: false en raíz y $defs.
    """
    raw_schema = schema_class.model_json_schema()
    schema_dict = copy.deepcopy(raw_schema)
    _ensure_additional_properties_false(schema_dict)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_class.__name__,
            "strict": True,
            "schema": schema_dict,
        },
    }


def parse_structured_json(raw_text: str, schema_class: Type[T]) -> T:
    """Parsea y valida conservadoramente una respuesta JSON o bloque cercado único contra un esquema Pydantic.

    Acepta:
    1. Cadena JSON plano directo representando un objeto JSON (dict).
    2. Exactamente un único bloque cercado (```json...``` o ```...```) con solo espacio en blanco alrededor.

    Rechaza con StructuredOutputParseError:
    - Prosa antes o después del JSON.
    - Múltiples bloques de código.
    - JSON que sea una lista o escalar de primer nivel en lugar de un objeto.
    - Comentarios trailing o extracción por expresiones regulares en texto libre.

    Lanza StructuredOutputValidationError si la estructura JSON no valida contra schema_class.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise StructuredOutputParseError(
            "La respuesta recibida de LM Studio está vacía o no es una cadena válida.",
            raw_response=raw_text if isinstance(raw_text, str) else None,
        )

    text = raw_text.strip()

    # Si contiene cercas de código Markdown
    if "```" in text:
        # Si tiene más de una apertura y cierre (ej. múltiples bloques), rechazar
        if text.count("```") != 2:
            raise StructuredOutputParseError(
                "La respuesta contiene múltiples bloques de código o cercas Markdown malformadas.",
                raw_response=raw_text,
            )

        match = _RE_SINGLE_FENCE.match(text)
        if not match:
            raise StructuredOutputParseError(
                "La respuesta contiene texto libre o comentarios circundantes alrededor del bloque de código.",
                raw_response=raw_text,
            )
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputParseError(
            f"Error de sintaxis JSON al parsear respuesta: {exc}",
            raw_response=raw_text,
        ) from exc

    if not isinstance(data, dict):
        raise StructuredOutputParseError(
            f"El JSON devuelto debe ser un objeto JSON (dict) para el esquema '{schema_class.__name__}', recibido: {type(data).__name__}",
            raw_response=raw_text,
        )

    try:
        return schema_class.model_validate_json(text)
    except ValidationError as exc:
        sanitized_err = sanitize_message(str(exc), max_chars=800)
        raise StructuredOutputValidationError(
            f"Error de validación contra esquema '{schema_class.__name__}': {sanitized_err}",
            raw_response=raw_text,
        ) from exc
