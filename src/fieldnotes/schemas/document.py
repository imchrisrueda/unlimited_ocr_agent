from pathlib import Path
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from .warnings import ExtractionWarning, JsonValue

BlockType = Literal[
    "text",
    "table",
    "field_sketch",
    "flowchart",
    "gps_sketch",
    "unknown",
]


class BlockIR(BaseModel):
    """Representación intermedia de un bloque de contenido en una página."""
    block_type: BlockType
    content: Optional[JsonValue] = None
    source_page: int = Field(..., ge=1)
    raw_text: Optional[str] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)


class PageIR(BaseModel):
    """Representación intermedia de una página estructurada."""
    page_number: int = Field(..., ge=1)
    image_path: Optional[Path] = None
    raw_ocr: Optional[str] = None
    blocks: list[BlockIR] = Field(default_factory=list)
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_page_ir_invariants(self) -> "PageIR":
        """Valida que todos los bloques y advertencias pertenezcan a esta página."""
        for idx, block in enumerate(self.blocks):
            if block.source_page != self.page_number:
                raise ValueError(
                    f"Inconsistencia de source_page en bloque {idx} de PageIR: "
                    f"esperado={self.page_number}, recibido={block.source_page}"
                )
        for idx, warning in enumerate(self.warnings):
            if warning.source_page is not None and warning.source_page != self.page_number:
                raise ValueError(
                    f"Inconsistencia de source_page en advertencia {idx} de PageIR: "
                    f"esperado={self.page_number}, recibido={warning.source_page}"
                )
        return self


class DocumentIR(BaseModel):
    """Representación intermedia agregada de un documento completo."""
    source_file: str
    pages: list[PageIR] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("pages")
    @classmethod
    def validate_unique_and_sort_pages(cls, pages: list[PageIR]) -> list[PageIR]:
        """Valida que los números de página sean únicos y los ordena deterministamente."""
        seen = set()
        for p in pages:
            if p.page_number in seen:
                raise ValueError(f"Número de página duplicado en DocumentIR: {p.page_number}")
            seen.add(p.page_number)
        return sorted(pages, key=lambda p: p.page_number)
