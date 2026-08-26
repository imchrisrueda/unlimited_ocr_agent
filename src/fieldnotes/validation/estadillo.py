import re
import math
from typing import List, Dict, Tuple
from collections import defaultdict
from src.fieldnotes.schemas.estadillo import EstadilloDocument, EstadilloRow
from src.fieldnotes.schemas.warnings import ExtractionWarning

# Expresión regular estricta para código BBCH de 2 dígitos ASCII (00..99)
_RE_BBCH_CANONICAL = re.compile(r"^[0-9]{2}$")


def validate_estadillo_document(
    doc: EstadilloDocument,
    min_height_cm: float = 0.0,
    max_height_cm: float = 500.0,
) -> EstadilloDocument:
    """Ejecuta un conjunto determinista y no destructivo de validaciones sobre EstadilloDocument.

    Validaciones implementadas (emiten ExtractionWarning sin alterar datos):
    1. DUPLICATE_COORDINATES: Dos o más filas comparten (col, fil) sobre enteros tipados válidos.
    2. SUSPICIOUS_ROW_SEQUENCE: Discontinuidad o inversión de sentido en la numeración de filas
       dentro de una misma columna.
    3. INVALID_BBCH_FORMAT: Código BBCH que no cumple el formato estricto de dos dígitos ASCII (00..99).
    4. MISSING_COORDINATES: Registro sin columna o fila identificable.
    5. SUSPICIOUS_HEIGHT: Altura fuera del rango biológico esperado (> min_height_cm y <= max_height_cm).

    Garantiza pureza e idempotencia: no muta el documento de entrada.
    """
    new_doc = doc.model_copy(deep=True)
    new_warnings: list[ExtractionWarning] = []

    # Recolectar todas las filas en orden de origen
    all_rows: list[EstadilloRow] = []
    for page in new_doc.pages:
        all_rows.extend(page.rows)

    # 1. DUPLICATE_COORDINATES
    coords_map: Dict[Tuple[int, int], list[EstadilloRow]] = defaultdict(list)
    for row in all_rows:
        if (
            row.col is not None
            and isinstance(row.col.normalized, int)
            and row.fil is not None
            and isinstance(row.fil.normalized, int)
        ):
            coords_map[(row.col.normalized, row.fil.normalized)].append(row)

    for (col_val, fil_val), matching_rows in coords_map.items():
        if len(matching_rows) > 1:
            warning = ExtractionWarning(
                code="DUPLICATE_COORDINATES",
                message=f"Coordenadas duplicadas (col={col_val}, fil={fil_val}) detectadas en {len(matching_rows)} filas.",
                severity="warning",
                source_page=matching_rows[0].source_page,
                field_name="col,fil",
                details={
                    "col": col_val,
                    "fil": fil_val,
                    "occurrences": [
                        {
                            "source_page": r.source_page,
                            "id": r.id.raw if r.id else None,
                        }
                        for r in matching_rows
                    ],
                },
            )
            new_warnings.append(warning)

    # 2. SUSPICIOUS_ROW_SEQUENCE
    # Agrupar filas por columna entera
    col_groups: Dict[int, list[EstadilloRow]] = defaultdict(list)
    for row in all_rows:
        if row.col is not None and isinstance(row.col.normalized, int):
            col_groups[row.col.normalized].append(row)

    for col_val, rows_in_col in col_groups.items():
        # Filtrar filas que tengan fil.normalized como entero válido
        fil_entries: list[tuple[int, EstadilloRow]] = []
        for r in rows_in_col:
            if r.fil is not None and isinstance(r.fil.normalized, int):
                fil_entries.append((r.fil.normalized, r))

        if len(fil_entries) >= 2:
            # Inferir dirección del primer paso unitario (+1 o -1)
            direction = None
            for idx in range(1, len(fil_entries)):
                delta = fil_entries[idx][0] - fil_entries[idx - 1][0]
                if abs(delta) == 1:
                    direction = 1 if delta > 0 else -1
                    break

            for idx in range(1, len(fil_entries)):
                prev_fil, prev_row = fil_entries[idx - 1]
                curr_fil, curr_row = fil_entries[idx]
                delta = curr_fil - prev_fil

                # Salto discontinuo
                if abs(delta) > 1:
                    warning = ExtractionWarning(
                        code="SUSPICIOUS_ROW_SEQUENCE",
                        message=(
                            f"Discontinuidad en secuencia de filas en columna {col_val}: "
                            f"salto de fil={prev_fil} a fil={curr_fil} (delta={delta})."
                        ),
                        severity="warning",
                        source_page=curr_row.source_page,
                        field_name="fil",
                        details={
                            "col": col_val,
                            "prev_fil": prev_fil,
                            "curr_fil": curr_fil,
                            "delta": delta,
                        },
                    )
                    new_warnings.append(warning)
                elif direction is not None and delta != 0:
                    current_dir = 1 if delta > 0 else -1
                    if current_dir != direction:
                        warning = ExtractionWarning(
                            code="SUSPICIOUS_ROW_SEQUENCE",
                            message=(
                                f"Inversión de dirección en secuencia de filas en columna {col_val}: "
                                f"cambio de paso (esperado={direction}, actual={delta})."
                            ),
                            severity="warning",
                            source_page=curr_row.source_page,
                            field_name="fil",
                            details={
                                "col": col_val,
                                "prev_fil": prev_fil,
                                "curr_fil": curr_fil,
                                "expected_direction": direction,
                            },
                        )
                        new_warnings.append(warning)

    # 3. INVALID_BBCH_FORMAT, 4. MISSING_COORDINATES, 5. SUSPICIOUS_HEIGHT
    for row in all_rows:
        # MISSING_COORDINATES
        has_col = row.col is not None and (row.col.raw is not None or row.col.normalized is not None)
        has_fil = row.fil is not None and (row.fil.raw is not None or row.fil.normalized is not None)
        if not has_col or not has_fil:
            missing_items = []
            if not has_col:
                missing_items.append("col")
            if not has_fil:
                missing_items.append("fil")
            missing_str = " y ".join(missing_items)
            row_id_str = f" (id={row.id.raw})" if row.id and row.id.raw else ""
            warning = ExtractionWarning(
                code="MISSING_COORDINATES",
                message=f"Fila sin coordenada {missing_str}{row_id_str}.",
                severity="warning",
                source_page=row.source_page,
                field_name="col,fil",
                details={"has_col": has_col, "has_fil": has_fil, "row_id": row.id.raw if row.id else None},
            )
            new_warnings.append(warning)

        # INVALID_BBCH_FORMAT
        if row.bbch is not None:
            bbch_val = (row.bbch.normalized or row.bbch.raw or "").strip()
            if bbch_val and not _RE_BBCH_CANONICAL.match(bbch_val):
                warning = ExtractionWarning(
                    code="INVALID_BBCH_FORMAT",
                    message=f"Formato BBCH no canónico: '{bbch_val}'. Se esperan 2 dígitos ASCII (00..99).",
                    severity="warning",
                    source_page=row.source_page,
                    field_name="bbch",
                    details={"raw": row.bbch.raw, "normalized": row.bbch.normalized},
                )
                new_warnings.append(warning)

        # SUSPICIOUS_HEIGHT
        if row.altura_cm is not None and row.altura_cm.normalized is not None:
            h = row.altura_cm.normalized
            if h <= min_height_cm or h > max_height_cm:
                warning = ExtractionWarning(
                    code="SUSPICIOUS_HEIGHT",
                    message=f"Altura {h} cm fuera de rango biológico ({min_height_cm} a {max_height_cm} cm).",
                    severity="warning",
                    source_page=row.source_page,
                    field_name="altura_cm",
                    details={"altura_cm": h, "min_height_cm": min_height_cm, "max_height_cm": max_height_cm},
                )
                new_warnings.append(warning)

    # Adjuntar advertencias deduplicadas
    def _warning_key(w: ExtractionWarning) -> tuple:
        detail_items = tuple(sorted((k, str(v)) for k, v in (w.details or {}).items()))
        return (w.code, w.source_page, w.field_name, w.message, detail_items)

    existing_keys = {_warning_key(w) for w in new_doc.warnings}
    for w in new_warnings:
        key = _warning_key(w)
        if key not in existing_keys:
            new_doc.warnings.append(w)
            existing_keys.add(key)

    return new_doc
