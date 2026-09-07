import hashlib
import json
import math
import posixpath
import re
from pathlib import Path
from typing import Optional, Any, Union
from pydantic import BaseModel, Field, ConfigDict, RootModel, model_validator, field_validator


class NormalizedBBox(BaseModel):
    """Coordenadas normalizadas [0.0, 1.0] relativas a la página (x0 < x1, y0 < y1)."""
    x0: float = Field(..., ge=0.0, le=1.0, description="Coordenada horizontal izquierda normalizada [0.0, 1.0]")
    y0: float = Field(..., ge=0.0, le=1.0, description="Coordenada vertical superior normalizada [0.0, 1.0]")
    x1: float = Field(..., ge=0.0, le=1.0, description="Coordenada horizontal derecha normalizada [0.0, 1.0]")
    y1: float = Field(..., ge=0.0, le=1.0, description="Coordenada vertical inferior normalizada [0.0, 1.0]")

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_box_invariants(self) -> "NormalizedBBox":
        """Valida que las coordenadas sean finitas, ordenadas y estrictamente crecientes."""
        if not (
            math.isfinite(self.x0)
            and math.isfinite(self.y0)
            and math.isfinite(self.x1)
            and math.isfinite(self.y1)
        ):
            raise ValueError("Las coordenadas del bounding box deben ser números finitos (no NaN ni Inf).")

        if self.x0 >= self.x1:
            raise ValueError(f"Coordenada x0 ({self.x0}) debe ser estrictamente menor que x1 ({self.x1}).")

        if self.y0 >= self.y1:
            raise ValueError(f"Coordenada y0 ({self.y0}) debe ser estrictamente menor que y1 ({self.y1}).")

        return self


def generate_issue_id(
    page: Optional[int],
    field: str,
    row_key: Optional[str] = None,
    warning_code: Optional[str] = None,
    reason: str = "",
) -> str:
    """Genera un identificador determinista, estable y reproducible para un ReviewIssue.

    No utiliza UUIDs aleatorios ni marcas de tiempo.
    """
    parts = [f"p{page:03d}" if page is not None else "doc"]
    if row_key:
        clean_row = re.sub(r"[^\w\-]", "_", str(row_key))
        parts.append(clean_row)
    clean_field = re.sub(r"[^\w\-]", "_", str(field))
    parts.append(clean_field)
    if warning_code:
        clean_code = re.sub(r"[^\w\-]", "_", str(warning_code))
        parts.append(clean_code)

    base_slug = "_".join(p for p in parts if p)
    # Hash sha256 truncado a 8 caracteres del contenido esencial para garantizar estabilidad e inequívoca unicidad
    hash_input = f"{page or ''}|{row_key or ''}|{field}|{warning_code or ''}|{reason}".encode("utf-8")
    hash_suffix = hashlib.sha256(hash_input).hexdigest()[:8]
    return f"{base_slug}_{hash_suffix}"


class ReviewIssue(BaseModel):
    """Contenedor estricto y auditable para incidencias o dudas que requieren revisión humana.

    Informa sobre discrepancias, incertidumbres o anomalías sin corregir automáticamente
    los datos ni sustituir los valores raw o normalized de la evidencia.
    """
    issue_id: str = Field(..., description="Identificador determinista y estable del issue")
    page: Optional[int] = Field(
        default=None,
        ge=1,
        description="Número de página fuente del issue (>= 1) o None para incidencias globales del documento",
    )
    field: str = Field(..., description="Campo o entidad afectada (ej: 'especie', 'bbch', 'col,fil')")
    row_key: Optional[str] = Field(default=None, description="Clave conservadora de fila (ej: 'col1_fil26', 'id_43') o None")
    reason: str = Field(..., description="Motivo descriptivo o mensaje del warning/incertidumbre")
    candidates: list[str] = Field(default_factory=list, description="Candidatos alternativos leídos directamente de la evidencia")
    crop_path: Optional[str] = Field(
        default=None,
        description="Ruta relativa POSIX al archivo PNG de crop dentro del documento ('review/....png') o None",
    )
    warning_code: Optional[str] = Field(default=None, description="Código de advertencia formal si procede")
    provenance: Optional[dict[str, Any]] = Field(default=None, description="Metadatos auditables de procedencia del issue")

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("crop_path")
    @classmethod
    def validate_crop_path_posix_relative(cls, v: Optional[str]) -> Optional[str]:
        """Valida que crop_path sea una ruta relativa POSIX estrictamente bajo el prefijo 'review/'."""
        if v is None:
            return None
        v_str = str(v).strip()
        if not v_str:
            return None

        # Prohibir backslashes de Windows en el JSON canónico
        if "\\" in v_str:
            raise ValueError(f"crop_path debe usar separadores POSIX '/' exclusivamente: '{v_str}'")

        # Prohibir rutas absolutas o con letra de unidad
        if v_str.startswith("/") or re.match(r"^[a-zA-Z]:", v_str):
            raise ValueError(f"crop_path debe ser una ruta relativa al documento: '{v_str}'")

        # Prohibir path traversal
        norm = posixpath.normpath(v_str)
        if norm.startswith("..") or "/../" in f"/{v_str}/":
            raise ValueError(f"crop_path no debe contener secuencias de escape '..': '{v_str}'")

        # Exigir estrictamente prefijo 'review/' (confinado bajo review/)
        if not (norm.startswith("review/") and len(norm) > len("review/")):
            raise ValueError(f"crop_path debe comenzar estrictamente con el prefijo 'review/': '{v_str}'")

        # Debe apuntar a un archivo con extensión .png
        if not norm.lower().endswith(".png"):
            raise ValueError(f"crop_path debe apuntar a un archivo PNG: '{v_str}'")

        return norm


ReviewIssuesList = RootModel[list[ReviewIssue]]


def review_issues_to_json(issues: list[ReviewIssue], indent: int = 2) -> str:
    """Serializa deterministamente una lista de ReviewIssue a JSON estricto."""
    root = ReviewIssuesList(issues)
    return root.model_dump_json(indent=indent)


def review_issues_from_json(json_str: str) -> list[ReviewIssue]:
    """Deserializa y valida estrictamente una lista de ReviewIssue desde un string JSON."""
    root = ReviewIssuesList.model_validate_json(json_str)
    return root.root