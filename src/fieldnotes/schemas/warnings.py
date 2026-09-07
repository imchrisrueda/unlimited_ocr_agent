from typing import Optional, Literal, Any, Union
from pydantic import BaseModel, Field, ConfigDict

JsonPrimitive = Union[None, bool, int, float, str]
JsonValue = Union[JsonPrimitive, list[Any], dict[str, Any]]


class ExtractionWarning(BaseModel):
    """Representación de una advertencia o incidencia durante la extracción estructurada."""
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    source_page: Optional[int] = Field(default=None, ge=1)
    field_name: Optional[str] = None
    details: Optional[dict[str, JsonValue]] = None

    model_config = ConfigDict(extra="forbid", strict=True)
