import copy
from typing import Optional, Dict
from src.fieldnotes.schemas.estadillo import EstadilloDocument
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.schemas.evidence import EvidenceValue

# Mapeos de especies autorizados por AGENTS.md (case-insensitive, trimmed)
_AUTHORIZED_SPECIES_MAP: Dict[str, str] = {
    "p": "P",
    "h": "H",
    "r": "R",
    "m": "M",
    "ap": "P",
    "ah": "H",
    "ar": "R",
    "mz": "M",
}


def normalize_species(doc: EstadilloDocument) -> EstadilloDocument:
    """Normaliza de forma pura e idempotente las especies en un EstadilloDocument.

    Aplica únicamente las transformaciones auditadas autorizadas:
    - Ap -> P
    - Ah -> H
    - Ar -> R
    - Mz -> M
    - P, H, R, M -> P, H, R, M

    Reglas estrictas:
    - Preserva siempre especie.raw inalterado.
    - Actualiza especie.normalized con el valor canónico.
    - Valores ambiguos como 'M2' o no reconocidos NO se auto-corrigen: se mantiene
      normalized=None y se emite una advertencia con código 'UNRECOGNIZED_SPECIES'.
    - No muta el documento de entrada (retorna una copia profunda).
    """
    new_doc = doc.model_copy(deep=True)
    new_warnings: list[ExtractionWarning] = []

    for page in new_doc.pages:
        for row in page.rows:
            if row.especie is not None:
                raw_val = row.especie.raw
                norm_key = raw_val.strip().lower() if raw_val is not None else ""

                if norm_key in _AUTHORIZED_SPECIES_MAP:
                    row.especie.normalized = _AUTHORIZED_SPECIES_MAP[norm_key]
                else:
                    # No alterar ni adivinar: dejar normalized como None y emitir advertencia
                    row.especie.normalized = None
                    col_val = row.col.normalized if row.col and row.col.normalized is not None else (row.col.raw if row.col else None)
                    fil_val = row.fil.normalized if row.fil and row.fil.normalized is not None else (row.fil.raw if row.fil else None)
                    warning = ExtractionWarning(
                        code="UNRECOGNIZED_SPECIES",
                        message=f"Especie no reconocida en catálogo estándar: '{raw_val}'",
                        severity="warning",
                        source_page=row.source_page,
                        field_name="especie",
                        details={
                            "raw": raw_val,
                            "row_id": row.id.raw if row.id and row.id.raw else None,
                            "col": col_val,
                            "fil": fil_val,
                        },
                    )
                    new_warnings.append(warning)

    # Añadir advertencias deduplicadas al documento
    existing_keys = {
        (w.code, w.source_page, w.field_name, w.message) for w in new_doc.warnings
    }
    for w in new_warnings:
        key = (w.code, w.source_page, w.field_name, w.message)
        if key not in existing_keys:
            new_doc.warnings.append(w)
            existing_keys.add(key)

    return new_doc


def complete_implied_coordinates(doc: EstadilloDocument) -> EstadilloDocument:
    """Complete coordinate cells implied by the fixed estadillo table layout.

    A blank ``col`` keeps the last explicit column until another column is
    explicitly written. A blank ``fil`` is completed only when a unitary
    ascending or descending sequence can be demonstrated from neighbouring
    explicit row values in that column. This operation is pure and idempotent.
    """
    new_doc = doc.model_copy(deep=True)

    for page in new_doc.pages:
        last_col: Optional[int] = None
        grouped_rows: Dict[int, list] = {}

        for row in page.rows:
            if row.col is not None and isinstance(row.col.normalized, int):
                last_col = row.col.normalized
            elif last_col is not None:
                row.col = EvidenceValue[int](
                    normalized=last_col,
                    source_page=row.source_page,
                )

            if row.col is not None and isinstance(row.col.normalized, int):
                grouped_rows.setdefault(row.col.normalized, []).append(row)

        for rows in grouped_rows.values():
            explicit = [
                (index, row.fil.normalized)
                for index, row in enumerate(rows)
                if row.fil is not None and isinstance(row.fil.normalized, int)
            ]
            directions = {
                1 if current_value > previous_value else -1
                for (previous_index, previous_value), (current_index, current_value) in zip(explicit, explicit[1:])
                if current_value != previous_value
                and abs(current_value - previous_value) == current_index - previous_index
            }
            if len(directions) != 1:
                continue
            direction = directions.pop()

            for index, row in enumerate(rows):
                if row.fil is not None and isinstance(row.fil.normalized, int):
                    continue
                previous = next(
                    ((known_index, value) for known_index, value in reversed(explicit) if known_index < index),
                    None,
                )
                following = next(
                    ((known_index, value) for known_index, value in explicit if known_index > index),
                    None,
                )
                if previous is not None:
                    inferred = previous[1] + direction * (index - previous[0])
                elif following is not None:
                    inferred = following[1] - direction * (following[0] - index)
                else:
                    continue
                row.fil = EvidenceValue[int](
                    normalized=inferred,
                    source_page=row.source_page,
                )

    return new_doc