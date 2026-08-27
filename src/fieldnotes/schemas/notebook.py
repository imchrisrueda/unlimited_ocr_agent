import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Optional, Literal, Union, List, Dict, Any, Set
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator

from .evidence import EvidenceValue
from .warnings import ExtractionWarning, JsonValue
from .document import DocumentIR, PageIR, BlockIR
from .estadillo import EstadilloPageHeader, EstadilloDocHeader, EstadilloRow
from .dto import (
    CandidateVisualBBox1000,
    VisualBBox1000,
    VisualRegion1000DTO,
    EstadilloHeaderDTO,
    EstadilloRowDTO,
    validate_visual_bbox1000,
    visual_bbox1000_to_normalized,
    _VALID_ROW_FIELDS,
    _convert_str_evidence,
    _convert_int_evidence,
    _convert_float_evidence,
)
from .diagram import DiagramIR, DiagramDTO, dto_to_diagram_ir


# =====================================================================
# Configuration Models for Notebook Profile
# =====================================================================

_VALID_HEADER_FIELDS = frozenset({
    "objetivo",
    "fecha",
    "asistentes",
    "equipamiento",
    "situacion_atmosferica",
    "especies_declaradas",
})

_VALID_SECTION_ORDER_KEYS = frozenset({
    "metadata",
    "estadillo_table",
    "sections",
    "notes",
    "tables",
    "diagrams",
    "additional_text",
})


def _normalize_name(name: str) -> str:
    """Normaliza un nombre o alias para comparación no ambigua (sin distinción de acentos, mayúsculas, espacios o guiones bajos)."""
    if not isinstance(name, str):
        name = str(name)
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", " ", s).strip().lower()


_CANONICAL_HEADER_MAP = {_normalize_name(f): f for f in _VALID_HEADER_FIELDS}


class NotebookSectionConfig(BaseModel):
    """Configuración tipada y estricta para una sección específica del cuaderno."""
    title: str = Field(..., min_length=1, description="Título formal y canónico de la sección")
    enabled: bool = Field(default=True, description="Indica si la sección debe incluirse en la salida Markdown")
    aliases: list[str] = Field(default_factory=list, description="Términos o variantes observadas que mapean a esta sección")
    include_if_empty: bool = Field(default=False, description="Incluir la sección aunque no se observe evidencia en el documento")
    render_order: Optional[int] = Field(default=None, ge=0, description="Orden explícito de renderizado (entero >= 0)")

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El título de la sección no puede estar vacío.")
        return s

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, aliases: list[str]) -> list[str]:
        seen = set()
        clean_aliases: list[str] = []
        for a in aliases:
            if not isinstance(a, str) or not a.strip():
                raise ValueError("Los alias de sección deben ser cadenas no vacías.")
            norm = _normalize_name(a)
            if norm in seen:
                raise ValueError(f"Alias duplicado '{a}' dentro de la misma sección.")
            seen.add(norm)
            clean_aliases.append(a.strip())
        return clean_aliases


class NotebookConfig(BaseModel):
    """Configuración tipada, versionada y estricta para el perfil general notebook (PR11)."""
    schema_version: Literal[1] = Field(default=1, description="Versión del esquema de configuración (estrictamente 1)")
    include_empty_sections: bool = Field(
        default=False,
        description="Por defecto False: no fabrica secciones vacías salvo solicitud explícita sin inventar contenido",
    )
    section_order: list[str] = Field(
        default_factory=lambda: [
            "metadata",
            "estadillo_table",
            "sections",
            "notes",
            "tables",
            "diagrams",
            "additional_text",
        ],
        description="Orden de presentación de los bloques de contenido (sin duplicados)",
    )
    section_configs: dict[str, NotebookSectionConfig] = Field(
        default_factory=dict,
        description="Configuración detallada por clave o título de sección",
    )
    header_aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Aliases para campos de cabecera observados",
    )
    render_diagram_visuals: bool = Field(
        default=True,
        description="Generar y enlazar representaciones visuales de diagramas (SVG). Si es False, conserva la descripción textual accesible sin crear enlaces rotos.",
    )
    species_normalization: bool = Field(
        default=True,
        description="Aplicar normalización no destructiva de especies (Ap->P, Ah->H, Ar->R, Mz->M) a filas tabulares",
    )
    validate_sequences: bool = True
    validate_bbch: bool = True
    validate_heights: bool = True
    validate_coordinates: bool = True
    min_height_cm: float = 0.0
    max_height_cm: float = 500.0

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("section_order")
    @classmethod
    def validate_section_order(cls, v: list[str]) -> list[str]:
        seen = set()
        for item in v:
            if item not in _VALID_SECTION_ORDER_KEYS:
                raise ValueError(
                    f"Clave de sección desconocida en section_order: '{item}' "
                    f"(permitidas: {sorted(_VALID_SECTION_ORDER_KEYS)})"
                )
            if item in seen:
                raise ValueError(f"Clave duplicada en section_order: '{item}'")
            seen.add(item)
        return v

    @field_validator("header_aliases")
    @classmethod
    def validate_header_aliases(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        global_seen_aliases: dict[str, str] = {}
        clean_header_aliases: dict[str, list[str]] = {}

        for field_name, aliases in v.items():
            norm_field = _normalize_name(field_name)
            if norm_field not in _CANONICAL_HEADER_MAP:
                raise ValueError(
                    f"Campo de cabecera desconocido '{field_name}' en header_aliases. "
                    f"Permitidos: {sorted(_VALID_HEADER_FIELDS)}"
                )
            canonical_field = _CANONICAL_HEADER_MAP[norm_field]

            clean_field_aliases: list[str] = []
            seen_in_this_field: set[str] = set()

            for a in aliases:
                if not isinstance(a, str) or not a.strip():
                    raise ValueError(f"Alias vacío o inválido en campo '{field_name}'.")
                norm_a = _normalize_name(a)
                if norm_a in seen_in_this_field:
                    raise ValueError(f"Alias duplicado '{a}' dentro del campo '{field_name}'.")
                seen_in_this_field.add(norm_a)

                # Si el alias es idéntico al propio campo canónico, omitir / deduplicar sin error
                if norm_a == norm_field:
                    continue

                # Si el alias coincide con OTRO campo canónico reservado, rechazar
                if norm_a in _CANONICAL_HEADER_MAP:
                    target_field = _CANONICAL_HEADER_MAP[norm_a]
                    raise ValueError(
                        f"El alias '{a}' en el campo '{field_name}' coincide con el nombre canónico del campo '{target_field}'. "
                        f"Los 6 nombres canónicos de cabecera son palabras reservadas y no pueden usarse como alias de otro campo."
                    )

                if norm_a in global_seen_aliases:
                    prev_field = global_seen_aliases[norm_a]
                    raise ValueError(
                        f"Alias ambiguo o duplicado '{a}': ya asignado al campo '{prev_field}'."
                    )

                global_seen_aliases[norm_a] = canonical_field
                clean_field_aliases.append(a.strip())

            clean_header_aliases[canonical_field] = clean_field_aliases

        return clean_header_aliases

    @field_validator("section_configs")
    @classmethod
    def validate_section_configs(cls, v: dict[str, NotebookSectionConfig]) -> dict[str, NotebookSectionConfig]:
        token_to_key: dict[str, str] = {}
        seen_render_orders: dict[int, str] = {}

        for key, sec_cfg in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Las claves de section_configs deben ser cadenas no vacías.")

            norm_key = _normalize_name(key)
            norm_title = _normalize_name(sec_cfg.title)

            config_tokens = {norm_key, norm_title}
            clean_aliases: list[str] = []
            seen_in_this_cfg: set[str] = set()

            for a in sec_cfg.aliases:
                norm_a = _normalize_name(a)
                if norm_a in seen_in_this_cfg:
                    raise ValueError(f"Alias duplicado '{a}' dentro de la sección '{key}'.")
                seen_in_this_cfg.add(norm_a)

                # Si el alias es idéntico a la clave o al título de la misma sección, se deduplica sin error
                if norm_a == norm_key or norm_a == norm_title:
                    continue

                config_tokens.add(norm_a)
                clean_aliases.append(a.strip())

            sec_cfg.aliases = clean_aliases

            for t in config_tokens:
                if t in token_to_key:
                    prev_key = token_to_key[t]
                    if prev_key != key:
                        raise ValueError(
                            f"Ambigüedad o colisión en section_configs entre '{key}' y '{prev_key}': "
                            f"el término '{t}' está compartido (como clave, título o alias). "
                            f"Cada sección debe poseer un namespace disjunto."
                        )
                token_to_key[t] = key

            if sec_cfg.render_order is not None:
                ro = sec_cfg.render_order
                if ro in seen_render_orders:
                    prev_key = seen_render_orders[ro]
                    raise ValueError(
                        f"Empate ambiguo en render_order={ro} entre secciones '{key}' y '{prev_key}'. "
                        f"Los valores de render_order deben ser estrictamente únicos si se especifican."
                    )
                seen_render_orders[ro] = key

        return v

    @model_validator(mode="after")
    def validate_height_ranges(self) -> "NotebookConfig":
        if not math.isfinite(self.min_height_cm):
            raise ValueError("min_height_cm debe ser un número finito.")
        if not math.isfinite(self.max_height_cm):
            raise ValueError("max_height_cm debe ser un número finito.")
        if self.min_height_cm < 0.0:
            raise ValueError(f"min_height_cm no puede ser negativo ({self.min_height_cm}).")
        if self.max_height_cm <= self.min_height_cm:
            raise ValueError(
                f"max_height_cm ({self.max_height_cm}) debe ser estrictamente mayor que min_height_cm ({self.min_height_cm})."
            )
        return self

    def get_prompt_guidance(self) -> str:
        """Genera un bloque de texto determinista y estructurado con guías de extracción para el VLM."""
        guidance_lines: list[str] = []

        if self.header_aliases:
            guidance_lines.append("ALIASES CONOCIDOS DE CABECERA (mapear al campo canónico correspondiente):")
            for field in sorted(self.header_aliases.keys()):
                aliases = self.header_aliases[field]
                if aliases:
                    guidance_lines.append(f"  - Campo '{field}': aliases reconocidos = {aliases}")

        if self.section_configs:
            guidance_lines.append("SECCIONES Y ALIASES PREFERENTES (clasificar en 'sections' usando estos títulos canónicos):")
            for sec_key in sorted(self.section_configs.keys()):
                sc = self.section_configs[sec_key]
                alias_str = f" (aliases: {sc.aliases})" if sc.aliases else ""
                guidance_lines.append(f"  - Sección '{sc.title}'{alias_str}")

        return "\n".join(guidance_lines)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "NotebookConfig":
        """Carga y valida la configuración desde un archivo JSON tipado."""
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Archivo de configuración no encontrado: {p}")
        content = p.read_text(encoding="utf-8")
        return cls.model_validate_json(content)


# =====================================================================
# Structured Extraction Models (DTOs) for General Notebook
# =====================================================================

class GenericTableDTO(BaseModel):
    """DTO compacto para tablas genéricas irregulares o heterogéneas."""
    title: Optional[str] = None
    headers: list[str] = Field(default_factory=list, description="Lista de cabeceras observadas, preservando posiciones")
    rows: list[list[Optional[str]]] = Field(default_factory=list, description="Matriz de filas y celdas")
    caption: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain_cells: list[list[int]] = Field(
        default_factory=list,
        description="Lista de coordenadas [row_idx, col_idx] de celdas dudosas (enteros >= 0)",
    )

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("uncertain_cells")
    @classmethod
    def validate_uncertain_cells_format(cls, v: list[list[int]]) -> list[list[int]]:
        seen = set()
        for idx, item in enumerate(v):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(
                    f"Cada entrada en uncertain_cells debe ser [row_idx, col_idx], recibido índice {idx}: {item!r}"
                )
            r, c = item[0], item[1]
            if not isinstance(r, int) or not isinstance(c, int) or isinstance(r, bool) or isinstance(c, bool):
                raise ValueError(f"Las coordenadas de celda deben ser enteros, recibido: [{r!r}, {c!r}]")
            if r < 0 or c < 0:
                raise ValueError(f"Las coordenadas de celda deben ser no negativas, recibido: [{r}, {c}]")
            if (r, c) in seen:
                raise ValueError(f"Coordenada duplicada en uncertain_cells: [{r}, {c}]")
            seen.add((r, c))
        return v

    @model_validator(mode="after")
    def validate_uncertain_cells_bounds(self) -> "GenericTableDTO":
        if not self.rows:
            if self.uncertain_cells:
                raise ValueError("uncertain_cells especifica celdas pero la tabla no contiene filas.")
            return self

        num_rows = len(self.rows)
        for r, c in self.uncertain_cells:
            if r >= num_rows:
                raise ValueError(
                    f"uncertain_cells contiene fila {r} fuera de rango (la tabla tiene {num_rows} filas)."
                )
            row_len = len(self.rows[r])
            if c >= row_len:
                raise ValueError(
                    f"uncertain_cells contiene columna {c} fuera de rango en fila {r} (longitud de fila: {row_len})."
                )
        return self


class NotebookSectionDTO(BaseModel):
    """DTO compacto para secciones o bloques de texto libre."""
    title: Optional[str] = None
    content: Optional[str] = None
    paragraphs: list[str] = Field(default_factory=list)
    level: int = Field(default=2, ge=1, le=6)
    raw_text: Optional[str] = None
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


class NotebookNoteItemDTO(BaseModel):
    """DTO compacto para notas breves o elementos de lista."""
    text: str = Field(..., min_length=1)
    category: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


class NotebookPageDTO(BaseModel):
    """Contrato estructurado compacto por página para el perfil general notebook."""
    page_number: int = Field(..., ge=1)
    header: Optional[EstadilloHeaderDTO] = None
    estadillo_rows: list[EstadilloRowDTO] = Field(default_factory=list)
    sections: list[NotebookSectionDTO] = Field(default_factory=list)
    notes: list[NotebookNoteItemDTO] = Field(default_factory=list)
    tables: list[GenericTableDTO] = Field(default_factory=list)
    diagrams: list[DiagramDTO] = Field(default_factory=list)
    additional_text: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    regions: list[VisualRegion1000DTO] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)


# =====================================================================
# Canonical Domain Models for General Notebook
# =====================================================================

class GenericTable(BaseModel):
    """Tabla genérica irregular o estructurada observada en el documento."""
    title: Optional[str] = None
    headers: list[EvidenceValue[str]] = Field(default_factory=list)
    rows: list[list[EvidenceValue[str]]] = Field(default_factory=list)
    caption: Optional[str] = None
    source_page: int = Field(..., ge=1)
    raw_text: Optional[str] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_table_source_pages(self) -> "GenericTable":
        for idx, h in enumerate(self.headers):
            if h.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en cabecera {idx} de GenericTable: "
                    f"esperado={self.source_page}, recibido={h.source_page}"
                )
        for r_idx, row in enumerate(self.rows):
            for c_idx, cell in enumerate(row):
                if cell.source_page != self.source_page:
                    raise ValueError(
                        f"Inconsistencia de source_page en celda [{r_idx}, {c_idx}] de GenericTable: "
                        f"esperado={self.source_page}, recibido={cell.source_page}"
                    )
        for idx, w in enumerate(self.warnings):
            if w.source_page is not None and w.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en advertencia {idx} de GenericTable: "
                    f"esperado={self.source_page}, recibido={w.source_page}"
                )
        return self


class NotebookSection(BaseModel):
    """Sección textual estructurada con título, contenido o párrafos."""
    title: Optional[str] = None
    content: Optional[str] = None
    paragraphs: list[EvidenceValue[str]] = Field(default_factory=list)
    level: int = Field(default=2, ge=1, le=6)
    source_page: int = Field(..., ge=1)
    raw_text: Optional[str] = None
    uncertain: bool = False
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_section_source_pages(self) -> "NotebookSection":
        for idx, p in enumerate(self.paragraphs):
            if p.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en párrafo {idx} de NotebookSection: "
                    f"esperado={self.source_page}, recibido={p.source_page}"
                )
        for idx, w in enumerate(self.warnings):
            if w.source_page is not None and w.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en advertencia {idx} de NotebookSection: "
                    f"esperado={self.source_page}, recibido={w.source_page}"
                )
        return self


class NotebookNoteItem(BaseModel):
    """Elemento individual de notas, observaciones o listas breves."""
    text: EvidenceValue[str]
    category: Optional[str] = None
    source_page: int = Field(..., ge=1)
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_note_source_page(self) -> "NotebookNoteItem":
        if self.text.source_page != self.source_page:
            raise ValueError(
                f"Inconsistencia de source_page en NotebookNoteItem: "
                f"esperado={self.source_page}, recibido={self.text.source_page}"
            )
        return self


class NotebookPage(BaseModel):
    """Representación canónica de una página dentro del perfil general notebook."""
    page_number: int = Field(..., ge=1)
    header: Optional[EstadilloPageHeader] = None
    estadillo_rows: list[EstadilloRow] = Field(default_factory=list)
    sections: list[NotebookSection] = Field(default_factory=list)
    notes: list[NotebookNoteItem] = Field(default_factory=list)
    tables: list[GenericTable] = Field(default_factory=list)
    diagrams: list[DiagramIR] = Field(default_factory=list)
    additional_text: Optional[str] = None
    warnings: list[ExtractionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_notebook_page_consistency(self) -> "NotebookPage":
        p = self.page_number
        if self.header is not None and self.header.source_page != p:
            raise ValueError(
                f"Inconsistencia de source_page en cabecera de página: esperado={p}, recibido={self.header.source_page}"
            )
        for idx, row in enumerate(self.estadillo_rows):
            if row.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en fila {idx}: esperado={p}, recibido={row.source_page}"
                )
        for idx, sec in enumerate(self.sections):
            if sec.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en sección {idx}: esperado={p}, recibido={sec.source_page}"
                )
        for idx, note in enumerate(self.notes):
            if note.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en nota {idx}: esperado={p}, recibido={note.source_page}"
                )
        for idx, tbl in enumerate(self.tables):
            if tbl.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en tabla {idx}: esperado={p}, recibido={tbl.source_page}"
                )
        for idx, diag in enumerate(self.diagrams):
            if diag.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en diagrama {idx}: esperado={p}, recibido={diag.source_page}"
                )
        for idx, w in enumerate(self.warnings):
            if w.source_page is not None and w.source_page != p:
                raise ValueError(
                    f"Inconsistencia de source_page en advertencia {idx}: esperado={p}, recibido={w.source_page}"
                )
        return self


class NotebookDocument(BaseModel):
    """Representación agregada y canónica de un documento completo de cuaderno de campo."""
    source_file: str
    pages: list[NotebookPage] = Field(default_factory=list)
    header: Optional[EstadilloDocHeader] = None
    estadillo_rows: list[EstadilloRow] = Field(default_factory=list)
    sections: list[NotebookSection] = Field(default_factory=list)
    notes: list[NotebookNoteItem] = Field(default_factory=list)
    tables: list[GenericTable] = Field(default_factory=list)
    diagrams: list[DiagramIR] = Field(default_factory=list)
    additional_texts: list[EvidenceValue[str]] = Field(default_factory=list)
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("pages")
    @classmethod
    def validate_unique_and_sort_pages(cls, pages: list[NotebookPage]) -> list[NotebookPage]:
        seen = set()
        for p in pages:
            if p.page_number in seen:
                raise ValueError(f"Número de página duplicado en NotebookDocument: {p.page_number}")
            seen.add(p.page_number)
        return sorted(pages, key=lambda p: p.page_number)

    def to_document_ir(self) -> DocumentIR:
        """Convierte este NotebookDocument a la representación intermedia genérica DocumentIR."""
        ir_pages: list[PageIR] = []
        for page in self.pages:
            blocks: list[BlockIR] = []
            if page.header is not None:
                blocks.append(
                    BlockIR(
                        block_type="table",
                        content=page.header.model_dump(),
                        source_page=page.page_number,
                        raw_text=None,
                        warnings=[],
                    )
                )
            if page.estadillo_rows:
                blocks.append(
                    BlockIR(
                        block_type="table",
                        content=[r.model_dump() for r in page.estadillo_rows],
                        source_page=page.page_number,
                        raw_text=None,
                        warnings=[],
                    )
                )
            for sec in page.sections:
                blocks.append(
                    BlockIR(
                        block_type="text",
                        content=sec.model_dump(),
                        source_page=page.page_number,
                        raw_text=sec.raw_text,
                        warnings=list(sec.warnings),
                    )
                )
            for note in page.notes:
                blocks.append(
                    BlockIR(
                        block_type="text",
                        content=note.model_dump(),
                        source_page=page.page_number,
                        raw_text=None,
                        warnings=[],
                    )
                )
            for tbl in page.tables:
                blocks.append(
                    BlockIR(
                        block_type="table",
                        content=tbl.model_dump(),
                        source_page=page.page_number,
                        raw_text=tbl.raw_text,
                        warnings=list(tbl.warnings),
                    )
                )
            for diag in page.diagrams:
                blocks.append(
                    BlockIR(
                        block_type=diag.diagram_type,
                        content=diag.model_dump(),
                        source_page=page.page_number,
                        raw_text=diag.raw_text,
                        warnings=list(diag.warnings),
                    )
                )
            ir_pages.append(
                PageIR(
                    page_number=page.page_number,
                    image_path=None,
                    raw_ocr=None,
                    blocks=blocks,
                    warnings=list(page.warnings),
                )
            )
        return DocumentIR(
            source_file=self.source_file,
            pages=ir_pages,
            metadata=self.metadata,
            warnings=list(self.warnings),
        )


# =====================================================================
# Conversion Function: DTO -> Canonical NotebookPage
# =====================================================================

def dto_to_notebook_page(
    dto: Union[NotebookPageDTO, NotebookPage],
    page_number: int,
) -> NotebookPage:
    """Convierte de forma pura y determinista un NotebookPageDTO al modelo canónico NotebookPage.

    Deriva e impone estrictamente source_page = page_number en todas las entidades hijas.
    Rechaza cualquier discrepancia entre dto.page_number y page_number.
    """
    if isinstance(dto, NotebookPage):
        if dto.page_number != page_number:
            raise ValueError(
                f"Discrepancia de número de página en NotebookPage: recibido={dto.page_number}, esperado={page_number}"
            )
        return dto.model_copy(deep=True)

    if dto.page_number != page_number:
        raise ValueError(
            f"Discrepancia de número de página en NotebookPageDTO: recibido={dto.page_number}, esperado={page_number}"
        )

    # 1. Cabecera
    canonical_header: Optional[EstadilloPageHeader] = None
    if dto.header is not None:
        empty_unc: frozenset[str] = frozenset()
        obj_ev = _convert_str_evidence(dto.header.objetivo, "objetivo", page_number, empty_unc)
        fec_ev = _convert_str_evidence(dto.header.fecha, "fecha", page_number, empty_unc)
        asi_ev = _convert_str_evidence(dto.header.asistentes, "asistentes", page_number, empty_unc)
        eq_ev = _convert_str_evidence(dto.header.equipamiento, "equipamiento", page_number, empty_unc)
        atm_ev = _convert_str_evidence(dto.header.situacion_atmosferica, "situacion_atmosferica", page_number, empty_unc)
        esp_ev = _convert_str_evidence(dto.header.especies_declaradas, "especies_declaradas", page_number, empty_unc)

        if any(ev is not None for ev in (obj_ev, fec_ev, asi_ev, eq_ev, atm_ev, esp_ev)):
            canonical_header = EstadilloPageHeader(
                source_page=page_number,
                objetivo=obj_ev,
                fecha=fec_ev,
                asistentes=asi_ev,
                equipamiento=eq_ev,
                situacion_atmosferica=atm_ev,
                especies_declaradas=esp_ev,
            )

    # 2. Filas de estadillo (si existen)
    canonical_rows: list[EstadilloRow] = []
    for r in dto.estadillo_rows:
        unc = frozenset(f for f in (r.uncertain_fields or []) if f in _VALID_ROW_FIELDS)
        row_id = _convert_str_evidence(r.id, "id", page_number, unc)
        row_col = _convert_int_evidence(r.col, "col", page_number, unc)
        row_fil = _convert_int_evidence(r.fil, "fil", page_number, unc)
        row_esp = _convert_str_evidence(r.especie, "especie", page_number, unc)
        row_alt = _convert_float_evidence(r.altura_cm, "altura_cm", page_number, unc)
        row_foto = _convert_str_evidence(r.foto, "foto", page_number, unc)
        row_bbch = _convert_str_evidence(r.bbch, "bbch", page_number, unc)
        row_obs = _convert_str_evidence(r.observaciones, "observaciones", page_number, unc)

        if any(ev is not None for ev in (row_id, row_col, row_fil, row_esp, row_alt, row_foto, row_bbch, row_obs)):
            canonical_rows.append(
                EstadilloRow(
                    source_page=page_number,
                    id=row_id,
                    col=row_col,
                    fil=row_fil,
                    especie=row_esp,
                    altura_cm=row_alt,
                    foto=row_foto,
                    bbch=row_bbch,
                    observaciones=row_obs,
                )
            )

    # 3. Secciones textuales
    canonical_sections: list[NotebookSection] = []
    for sec in dto.sections:
        p_evs: list[EvidenceValue[str]] = []
        for p_str in sec.paragraphs:
            if p_str and str(p_str).strip():
                p_clean = str(p_str).strip()
                p_evs.append(
                    EvidenceValue[str](
                        raw=p_clean,
                        normalized=p_clean,
                        source_page=page_number,
                        uncertain=sec.uncertain,
                    )
                )
        if sec.title or sec.content or p_evs or sec.raw_text:
            canonical_sections.append(
                NotebookSection(
                    title=sec.title.strip() if sec.title else None,
                    content=sec.content.strip() if sec.content else None,
                    paragraphs=p_evs,
                    level=sec.level,
                    source_page=page_number,
                    raw_text=sec.raw_text,
                    uncertain=sec.uncertain,
                )
            )

    # 4. Notas y observaciones sueltas
    canonical_notes: list[NotebookNoteItem] = []
    for note in dto.notes:
        if note.text and note.text.strip():
            canonical_notes.append(
                NotebookNoteItem(
                    text=EvidenceValue[str](
                        raw=note.text.strip(),
                        normalized=note.text.strip(),
                        source_page=page_number,
                        uncertain=note.uncertain,
                    ),
                    category=note.category.strip() if note.category else None,
                    source_page=page_number,
                    uncertain=note.uncertain,
                )
            )

    # 5. Tablas genéricas (preservando posiciones y celdas vacías sin desplazar columnas)
    canonical_tables: list[GenericTable] = []
    for tbl in dto.tables:
        unc_set = {(c[0], c[1]) for c in tbl.uncertain_cells if len(c) == 2}
        h_evs: list[EvidenceValue[str]] = [
            EvidenceValue[str](
                raw=str(h).strip() if h is not None else "",
                normalized=str(h).strip() if h is not None else "",
                source_page=page_number,
                uncertain=False,
            )
            for h in tbl.headers
        ]
        r_evs: list[list[EvidenceValue[str]]] = []
        for r_idx, row in enumerate(tbl.rows):
            row_cells: list[EvidenceValue[str]] = []
            for c_idx, cell_val in enumerate(row):
                cell_raw = str(cell_val).strip() if cell_val is not None else ""
                is_unc = (r_idx, c_idx) in unc_set
                row_cells.append(
                    EvidenceValue[str](
                        raw=cell_raw,
                        normalized=cell_raw,
                        source_page=page_number,
                        uncertain=is_unc,
                    )
                )
            r_evs.append(row_cells)

        if h_evs or r_evs or tbl.title or tbl.raw_text:
            canonical_tables.append(
                GenericTable(
                    title=tbl.title.strip() if tbl.title else None,
                    headers=h_evs,
                    rows=r_evs,
                    caption=tbl.caption.strip() if tbl.caption else None,
                    source_page=page_number,
                    raw_text=tbl.raw_text,
                    warnings=[],
                    uncertain=bool(unc_set),
                )
            )

    # 6. Diagramas
    canonical_diagrams: list[DiagramIR] = []
    for diag_dto in dto.diagrams:
        canonical_diag = dto_to_diagram_ir(diag_dto, source_page=page_number)
        canonical_diagrams.append(canonical_diag)

    # 7. Advertencias
    canonical_warnings = [
        ExtractionWarning(code="VLM_WARNING", message=str(w), source_page=page_number)
        for w in (dto.warnings or [])
        if str(w).strip()
    ]

    return NotebookPage(
        page_number=page_number,
        header=canonical_header,
        estadillo_rows=canonical_rows,
        sections=canonical_sections,
        notes=canonical_notes,
        tables=canonical_tables,
        diagrams=canonical_diagrams,
        additional_text=dto.additional_text.strip() if dto.additional_text else None,
        warnings=canonical_warnings,
    )