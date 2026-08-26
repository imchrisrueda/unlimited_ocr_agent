import copy
from typing import Optional, Dict
from src.fieldnotes.schemas.estadillo import EstadilloDocument
from src.fieldnotes.schemas.warnings import ExtractionWarning

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
                    warning = ExtractionWarning(
                        code="UNRECOGNIZED_SPECIES",
                        message=f"Especie no reconocida en catálogo estándar: '{raw_val}'",
                        severity="warning",
                        source_page=row.source_page,
                        field_name="especie",
                        details={
                            "raw": raw_val,
                            "row_id": row.id.raw if row.id and row.id.raw else None,
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
