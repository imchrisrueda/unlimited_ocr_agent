import math
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from .evidence import EvidenceValue
from .warnings import ExtractionWarning


class _EstadilloHeaderBase(BaseModel):
    """Estructura base para los metadatos de cabecera de notas de campo (primera tabla)."""
    objetivo: Optional[EvidenceValue[str]] = None
    fecha: Optional[EvidenceValue[str]] = None
    asistentes: Optional[EvidenceValue[str]] = None
    equipamiento: Optional[EvidenceValue[str]] = None
    situacion_atmosferica: Optional[EvidenceValue[str]] = None
    especies_declaradas: Optional[EvidenceValue[str]] = None

    model_config = ConfigDict(extra="forbid", strict=True)


class EstadilloPageHeader(_EstadilloHeaderBase):
    """Metadatos de la cabecera extraídos de una página individual (EstadilloPage).

    El campo `source_page` es estrictamente obligatorio, no nulo e idéntico al
    número de página analizada, forzando un contrato inequívoco en el JSON Schema.
    """
    source_page: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_header_evidence_source_pages(self) -> "EstadilloPageHeader":
        """Valida que todos los campos de evidencia pertenezcan estrictamente a source_page."""
        for field_name in (
            "objetivo",
            "fecha",
            "asistentes",
            "equipamiento",
            "situacion_atmosferica",
            "especies_declaradas",
        ):
            ev: Optional[EvidenceValue[str]] = getattr(self, field_name)
            if ev is not None and ev.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en cabecera campo '{field_name}': "
                    f"esperado={self.source_page}, recibido={ev.source_page}"
                )
        return self


class EstadilloDocHeader(_EstadilloHeaderBase):
    """Metadatos de la cabecera agregada/reconciliada de un documento completo (EstadilloDocument).

    En la cabecera agregada, `source_page` es None por defecto ya que la cabecera puede
    provenir de múltiples páginas fusionadas, conservando cada EvidenceValue la procedencia
    de su página ganadora original.
    """
    source_page: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_header_evidence_source_pages(self) -> "EstadilloDocHeader":
        """Valida que si source_page está definido, todos los campos de evidencia pertenezcan a él."""
        if self.source_page is not None:
            for field_name in (
                "objetivo",
                "fecha",
                "asistentes",
                "equipamiento",
                "situacion_atmosferica",
                "especies_declaradas",
            ):
                ev: Optional[EvidenceValue[str]] = getattr(self, field_name)
                if ev is not None and ev.source_page != self.source_page:
                    raise ValueError(
                        f"Inconsistencia de source_page en cabecera agregada campo '{field_name}': "
                        f"esperado={self.source_page}, recibido={ev.source_page}"
                    )
        return self


# Alias canónicos para compatibilidad y semántica
EstadilloHeader = EstadilloDocHeader
EstadilloDocumentHeader = EstadilloDocHeader


class EstadilloRow(BaseModel):
    """Registro o fila individual de la tabla de notas de campo (segunda tabla)."""
    id: Optional[EvidenceValue[str]] = None
    col: Optional[EvidenceValue[int]] = None
    fil: Optional[EvidenceValue[int]] = None
    especie: Optional[EvidenceValue[str]] = None
    altura_cm: Optional[EvidenceValue[float]] = None
    foto: Optional[EvidenceValue[str]] = None
    bbch: Optional[EvidenceValue[str]] = None
    observaciones: Optional[EvidenceValue[str]] = None
    source_page: int = Field(..., ge=1)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("altura_cm")
    @classmethod
    def validate_altura_cm_finite(cls, v: Optional[EvidenceValue[float]]) -> Optional[EvidenceValue[float]]:
        """Asegura que los valores normalizados de altura_cm sean números reales finitos."""
        if v is not None and v.normalized is not None:
            if not isinstance(v.normalized, (int, float)) or not math.isfinite(v.normalized):
                raise ValueError("El valor normalizado de altura_cm debe ser un número finito (no NaN ni Inf)")
        return v

    @model_validator(mode="after")
    def validate_row_evidence_source_pages(self) -> "EstadilloRow":
        """Valida que cada campo de evidencia presente coincida con el source_page de la fila."""
        for field_name in (
            "id",
            "col",
            "fil",
            "especie",
            "altura_cm",
            "foto",
            "bbch",
            "observaciones",
        ):
            ev = getattr(self, field_name)
            if ev is not None and ev.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en fila campo '{field_name}': "
                    f"esperado={self.source_page}, recibido={ev.source_page}"
                )
        return self


class EstadilloPage(BaseModel):
    """Estructura tipada de extracción para una página individual de notas de campo."""
    page_number: int = Field(..., ge=1)
    header: Optional[EstadilloPageHeader] = None
    rows: list[EstadilloRow] = Field(default_factory=list)
    additional_text: Optional[str] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_page_consistency(self) -> "EstadilloPage":
        """Valida que los source_page de filas, cabecera y advertencias coincidan con page_number."""
        if self.header is not None:
            if self.header.source_page != self.page_number:
                raise ValueError(
                    f"Inconsistencia de source_page en cabecera de página: esperado={self.page_number}, "
                    f"recibido={self.header.source_page}"
                )
        for idx, row in enumerate(self.rows):
            if row.source_page != self.page_number:
                raise ValueError(
                    f"Inconsistencia de source_page en fila {idx}: esperado={self.page_number}, "
                    f"recibido={row.source_page}"
                )
        for idx, warning in enumerate(self.warnings):
            if warning.source_page is not None and warning.source_page != self.page_number:
                raise ValueError(
                    f"Inconsistencia de source_page en advertencia {idx}: esperado={self.page_number}, "
                    f"recibido={warning.source_page}"
                )
        return self

    @property
    def total_records(self) -> int:
        """Número de filas/registros de datos en la página."""
        return len(self.rows)


class EstadilloDocument(BaseModel):
    """Estructura agregada de un documento completo de notas de campo (estadillo)."""
    source_file: str
    pages: list[EstadilloPage] = Field(default_factory=list)
    header: Optional[EstadilloDocHeader] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    additional_texts: list[EvidenceValue[str]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @property
    def total_records(self) -> int:
        """Total agregado de filas/registros de datos en todas las páginas del documento."""
        return sum(len(p.rows) for p in self.pages)

    @field_validator("pages")
    @classmethod
    def validate_unique_and_sort_pages(cls, pages: list[EstadilloPage]) -> list[EstadilloPage]:
        """Valida que los números de página sean únicos y los ordena deterministamente."""
        seen = set()
        for p in pages:
            if p.page_number in seen:
                raise ValueError(f"Número de página duplicado en EstadilloDocument: {p.page_number}")
            seen.add(p.page_number)
        return sorted(pages, key=lambda p: p.page_number)
