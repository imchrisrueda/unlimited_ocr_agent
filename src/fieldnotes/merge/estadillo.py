import re
from typing import Optional, List, Dict, Any
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.schemas.estadillo import (
    EstadilloPage,
    EstadilloPageHeader,
    EstadilloDocHeader,
    EstadilloHeader,
    EstadilloDocument,
)


def _normalize_ws(s: Optional[str]) -> str:
    """Colapsa cualquier secuencia de espacios en blanco Unicode a un único espacio ASCII y recorta extremos."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def merge_estadillo_pages(pages: list[EstadilloPage], source_file: str) -> EstadilloDocument:
    """Fusiona deterministamente una lista de páginas EstadilloPage en un EstadilloDocument.

    Garantiza:
    1. Ordenación determinista de páginas por page_number.
    2. Conservación íntegra y ordenada de todas las filas en el orden de origen.
    3. Reconciliación de metadatos de cabecera sin sobreescrituras silenciosas:
       - Si no hay datos, el campo queda como None.
       - Si un único valor está presente o todos los posteriores son idénticos
         (bajo comparación con colapso determinista de espacios en blanco), se adopta dicho valor.
       - Si existen discrepancias entre páginas, se retiene el valor de la primera
         página que lo contenga y se emite una ExtractionWarning con código 'HEADER_CONFLICT'.
       - Se preserva la procedencia genuina (source_page) de cada EvidenceValue ganador sin reescribirla.
    4. Extracción de additional_text de cada página como EvidenceValue ordenado por página.
    5. Agregación deduplicada de advertencias.
    6. Pureza e idempotencia: no muta las páginas de entrada.
    """
    if not pages:
        return EstadilloDocument(source_file=source_file, pages=[])

    sorted_pages = sorted(pages, key=lambda p: p.page_number)
    copied_pages = [p.model_copy(deep=True) for p in sorted_pages]

    doc_warnings: list[ExtractionWarning] = []
    seen_warning_keys = set()

    # Recolectar advertencias preexistentes en páginas
    for page in copied_pages:
        for w in page.warnings:
            key = (w.code, w.source_page, w.field_name, w.message)
            if key not in seen_warning_keys:
                doc_warnings.append(w.model_copy(deep=True))
                seen_warning_keys.add(key)

    # Reconciliación de cabecera
    header_field_names = [
        "objetivo",
        "fecha",
        "asistentes",
        "equipamiento",
        "situacion_atmosferica",
        "especies_declaradas",
    ]

    reconciled_fields: Dict[str, Optional[EvidenceValue[str]]] = {}
    any_header_found = False

    for field_name in header_field_names:
        entries: list[tuple[int, EvidenceValue[str]]] = []
        for p in copied_pages:
            if p.header is not None:
                ev: Optional[EvidenceValue[str]] = getattr(p.header, field_name)
                if ev is not None and (ev.raw is not None or ev.normalized is not None):
                    entries.append((p.page_number, ev))

        if not entries:
            reconciled_fields[field_name] = None
            continue

        any_header_found = True
        first_page, first_ev = entries[0]
        first_cmp = _normalize_ws(first_ev.normalized or first_ev.raw)

        # Comprobar si hay discrepancias con páginas posteriores
        conflicts: list[dict[str, Any]] = []
        for other_page, other_ev in entries[1:]:
            other_cmp = _normalize_ws(other_ev.normalized or other_ev.raw)
            if other_cmp != first_cmp:
                conflicts.append({
                    "page": other_page,
                    "value": other_ev.normalized or other_ev.raw,
                })

        if conflicts:
            conflict_warning = ExtractionWarning(
                code="HEADER_CONFLICT",
                message=(
                    f"Discrepancia de cabecera en el campo '{field_name}' entre páginas. "
                    f"Se retiene el valor de la página {first_page}."
                ),
                severity="warning",
                source_page=first_page,
                field_name=field_name,
                details={
                    "winner_page": first_page,
                    "winner_value": first_ev.normalized or first_ev.raw,
                    "conflicts": conflicts,
                },
            )
            key = (
                conflict_warning.code,
                conflict_warning.source_page,
                conflict_warning.field_name,
                conflict_warning.message,
            )
            if key not in seen_warning_keys:
                doc_warnings.append(conflict_warning)
                seen_warning_keys.add(key)

        # Preservar el EvidenceValue ganador con su source_page genuina intacta
        winner_ev = first_ev.model_copy(deep=True)
        reconciled_fields[field_name] = winner_ev

    reconciled_header: Optional[EstadilloHeader] = None
    if any_header_found:
        reconciled_header = EstadilloHeader(
            objetivo=reconciled_fields["objetivo"],
            fecha=reconciled_fields["fecha"],
            asistentes=reconciled_fields["asistentes"],
            equipamiento=reconciled_fields["equipamiento"],
            situacion_atmosferica=reconciled_fields["situacion_atmosferica"],
            especies_declaradas=reconciled_fields["especies_declaradas"],
            source_page=None,
        )

    # Recolectar additional_text
    additional_texts: list[EvidenceValue[str]] = []
    for page in copied_pages:
        if page.additional_text and page.additional_text.strip():
            text_str = page.additional_text.strip()
            additional_texts.append(
                EvidenceValue[str](
                    raw=text_str,
                    normalized=text_str,
                    source_page=page.page_number,
                    uncertain=False,
                )
            )

    return EstadilloDocument(
        source_file=source_file,
        pages=copied_pages,
        header=reconciled_header,
        warnings=doc_warnings,
        additional_texts=additional_texts,
    )
