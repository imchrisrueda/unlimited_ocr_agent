import re
from typing import Optional, List, Dict, Any

from ..schemas.evidence import EvidenceValue
from ..schemas.warnings import ExtractionWarning
from ..schemas.estadillo import EstadilloDocHeader, EstadilloRow
from ..schemas.notebook import (
    NotebookConfig,
    NotebookPage,
    NotebookDocument,
    NotebookSection,
    NotebookNoteItem,
    GenericTable,
)
from ..schemas.diagram import DiagramIR


def _normalize_ws(text: Optional[str]) -> str:
    """Normaliza espacios en blanco internos y extremos para comparación de cadenas."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def merge_notebook_pages(
    pages: list[NotebookPage],
    source_file: str,
    config: Optional[NotebookConfig] = None,
) -> NotebookDocument:
    """Fusiona de forma pura, determinista y no mutativa múltiples NotebookPages en un NotebookDocument.

    Garantías:
    1. Inmutabilidad: No modifica los objetos NotebookPage de entrada; genera nuevas instancias.
    2. Ordenación determinista: Ordena estrictamente las páginas por `page_number`.
    3. Reconciliación de cabecera:
       - Si los valores coinciden bajo normalización de espacios, adopta el primer valor con su source_page original.
       - Si existen valores conflictivos entre páginas, retiene el primero y emite ExtractionWarning(code='HEADER_CONFLICT').
    4. Conservación documental: Concatena filas de estadillo, secciones, notas, tablas genéricas y diagramas
       en estricto orden de procedencia sin pérdida de información ni inventar datos.
    5. Trazabilidad de textos adicionales: Extrae additional_text por página como EvidenceValue con su source_page.
    """
    if not pages:
        return NotebookDocument(source_file=source_file, pages=[])

    sorted_pages = sorted(pages, key=lambda p: p.page_number)
    merged_warnings: list[ExtractionWarning] = []

    # 1. Reconciliación de metadatos de cabecera
    header_fields = (
        "objetivo",
        "fecha",
        "asistentes",
        "equipamiento",
        "situacion_atmosferica",
        "especies_declaradas",
    )
    field_evidence_map: dict[str, list[EvidenceValue[str]]] = {f: [] for f in header_fields}

    for page in sorted_pages:
        # Extraer advertencias a nivel de página
        merged_warnings.extend(page.warnings)

        if page.header is not None:
            for field_name in header_fields:
                ev = getattr(page.header, field_name)
                if ev is not None:
                    field_evidence_map[field_name].append(ev)

    reconciled_header_dict: dict[str, Optional[EvidenceValue[str]]] = {}

    for field_name, ev_list in field_evidence_map.items():
        if not ev_list:
            reconciled_header_dict[field_name] = None
            continue

        winner = ev_list[0]
        winner_norm = _normalize_ws(winner.normalized or winner.raw)
        conflicts: list[dict[str, Any]] = []

        for other in ev_list[1:]:
            other_norm = _normalize_ws(other.normalized or other.raw)
            if other_norm != winner_norm:
                conflicts.append(
                    {
                        "page": other.source_page,
                        "value": other.raw or other.normalized,
                    }
                )

        if conflicts:
            merged_warnings.append(
                ExtractionWarning(
                    code="HEADER_CONFLICT",
                    message=(
                        f"Discrepancia en metadatos de cabecera campo '{field_name}'. "
                        f"Se retiene el valor de la página {winner.source_page} ({winner.raw or winner.normalized!r}). "
                        f"Valores en conflicto observados: {conflicts}"
                    ),
                    severity="warning",
                    source_page=None,
                    field_name=field_name,
                    details={
                        "field": field_name,
                        "winner_page": winner.source_page,
                        "winner_value": winner.raw or winner.normalized,
                        "conflicts": conflicts,
                    },
                )
            )

        reconciled_header_dict[field_name] = winner.model_copy(deep=True)

    has_any_header_val = any(v is not None for v in reconciled_header_dict.values())
    aggregated_header = (
        EstadilloDocHeader(source_page=None, **reconciled_header_dict)
        if has_any_header_val
        else None
    )

    # 2. Concatena filas de estadillo
    all_estadillo_rows: list[EstadilloRow] = []
    for page in sorted_pages:
        for r in page.estadillo_rows:
            all_estadillo_rows.append(r.model_copy(deep=True))

    # 3. Concatena secciones
    all_sections: list[NotebookSection] = []
    for page in sorted_pages:
        for sec in page.sections:
            all_sections.append(sec.model_copy(deep=True))

    # 4. Concatena notas
    all_notes: list[NotebookNoteItem] = []
    for page in sorted_pages:
        for note in page.notes:
            all_notes.append(note.model_copy(deep=True))

    # 5. Concatena tablas genéricas
    all_tables: list[GenericTable] = []
    for page in sorted_pages:
        for tbl in page.tables:
            all_tables.append(tbl.model_copy(deep=True))

    # 6. Concatena diagramas
    all_diagrams: list[DiagramIR] = []
    for page in sorted_pages:
        for diag in page.diagrams:
            all_diagrams.append(diag.model_copy(deep=True))

    # 7. Textos adicionales fuera de bloques
    additional_texts: list[EvidenceValue[str]] = []
    for page in sorted_pages:
        if page.additional_text and page.additional_text.strip():
            raw_t = page.additional_text.strip()
            additional_texts.append(
                EvidenceValue[str](
                    raw=raw_t,
                    normalized=raw_t,
                    source_page=page.page_number,
                )
            )

    return NotebookDocument(
        source_file=source_file,
        pages=[p.model_copy(deep=True) for p in sorted_pages],
        header=aggregated_header,
        estadillo_rows=all_estadillo_rows,
        sections=all_sections,
        notes=all_notes,
        tables=all_tables,
        diagrams=all_diagrams,
        additional_texts=additional_texts,
        warnings=merged_warnings,
    )