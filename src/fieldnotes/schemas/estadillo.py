from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from .evidence import EvidenceValue
from .warnings import ExtractionWarning


class EstadilloHeader(BaseModel):
    """Metadatos de la cabecera del cuaderno de campo (primera tabla)."""
    objetivo: Optional[EvidenceValue[str]] = None
    fecha: Optional[EvidenceValue[str]] = None
    asistentes: Optional[EvidenceValue[str]] = None
    equipamiento: Optional[EvidenceValue[str]] = None
    situacion_atmosferica: Optional[EvidenceValue[str]] = None
    especies_declaradas: Optional[EvidenceValue[str]] = None
    source_page: int = Field(..., ge=1)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_header_evidence_source_pages(self) -> "EstadilloHeader":
        """Valida que todos los campos de evidencia presentes pertenezcan a source_page."""
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
    header: Optional[EstadilloHeader] = None
    rows: list[EstadilloRow] = Field(default_factory=list)
    additional_text: Optional[str] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_page_consistency(self) -> "EstadilloPage":
        """Valida que los source_page de filas, cabecera y advertencias coincidan con page_number."""
        if self.header is not None and self.header.source_page != self.page_number:
            raise ValueError(
                f"Inconsistencia de source_page en cabecera: esperado={self.page_number}, "
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


class EstadilloDocument(BaseModel):
    """Contrato inicial para la colección de páginas de un documento de estadillos."""
    source_file: str
    pages: list[EstadilloPage] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

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
