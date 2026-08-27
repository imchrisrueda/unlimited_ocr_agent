import math
import re
from typing import Optional, List, Dict, Tuple, Set

from ..schemas.evidence import EvidenceValue
from ..schemas.warnings import ExtractionWarning
from ..schemas.estadillo import EstadilloRow
from ..schemas.notebook import NotebookConfig, NotebookDocument, GenericTable
from ..schemas.diagram import DiagramIR
_BBCH_CANONICAL_RE = re.compile(r"^[0-9]{2}$")

_SPECIES_NORMALIZATION_MAP = {
    "ap": "P",
    "ah": "H",
    "ar": "R",
    "mz": "M",
    "p": "P",
    "h": "H",
    "r": "R",
    "m": "M",
}


def _normalize_single_species(
    row: EstadilloRow,
) -> Tuple[EstadilloRow, Optional[ExtractionWarning]]:
    """Normaliza de forma auditable la especie de una fila según AGENTS.md."""
    if row.especie is None:
        return row, None

    ev = row.especie
    raw_str = (ev.raw or "").strip()
    if not raw_str:
        return row, None

    key = raw_str.lower()
    norm_val = _SPECIES_NORMALIZATION_MAP.get(key)

    if norm_val is not None:
        new_ev = EvidenceValue[str](
            raw=ev.raw,
            normalized=norm_val,
            source_page=ev.source_page,
            uncertain=ev.uncertain,
            alternatives=list(ev.alternatives),
        )
        new_row = row.model_copy(update={"especie": new_ev}, deep=True)
        return new_row, None
    else:
        new_ev = EvidenceValue[str](
            raw=ev.raw,
            normalized=None,
            source_page=ev.source_page,
            uncertain=True,
            alternatives=list(ev.alternatives),
        )
        new_row = row.model_copy(update={"especie": new_ev}, deep=True)
        warn = ExtractionWarning(
            code="UNRECOGNIZED_SPECIES",
            message=(
                f"Especie {raw_str!r} no reconocida en catálogo autorizado (P, H, R, M). "
                f"Se conserva valor raw sin normalización automática."
            ),
            severity="warning",
            source_page=ev.source_page,
            field_name="especie",
            details={"raw_species": raw_str},
        )
        return new_row, warn


def validate_notebook_document(
    doc: NotebookDocument,
    config: Optional[NotebookConfig] = None,
) -> NotebookDocument:
    """Valida y enriquece con ExtractionWarnings un NotebookDocument de forma pura y determinista.

    Garantías:
    1. Inmutabilidad: Devuelve una nueva copia profunda sin mutar el documento original.
    2. Normalización de especies: Aplica la política no destructiva a filas tabulares.
    3. Detección de anomalías en filas: Duplicados (col, fil), saltos de secuencia, formato BBCH y rangos de altura.
    4. Validación de tablas genéricas: Detecta discrepancias de longitud en filas y celdas irregulares.
    5. Validación de DiagramIR: Verifica IDs únicos, referencias y separación visual vs GPS.
    """
    cfg = config or NotebookConfig()
    new_doc = doc.model_copy(deep=True)
    added_warnings: list[ExtractionWarning] = []

    # 1. Normalización de especies en filas de estadillo
    normalized_rows: list[EstadilloRow] = []
    for r in new_doc.estadillo_rows:
        if cfg.species_normalization:
            norm_r, sp_warn = _normalize_single_species(r)
            if sp_warn is not None:
                added_warnings.append(sp_warn)
            normalized_rows.append(norm_r)
        else:
            normalized_rows.append(r)

    new_doc.estadillo_rows = normalized_rows

    # También actualizar en páginas hijas
    for page in new_doc.pages:
        page_norm_rows: list[EstadilloRow] = []
        for r in page.estadillo_rows:
            if cfg.species_normalization:
                norm_r, _ = _normalize_single_species(r)
                page_norm_rows.append(norm_r)
            else:
                page_norm_rows.append(r)
        page.estadillo_rows = page_norm_rows

    # 2. Validadores tabulares de estadillo
    if cfg.validate_coordinates:
        # Detección de duplicados (col, fil) exclusivamente sobre enteros tipados válidos
        seen_coords: dict[tuple[int, int], list[int]] = {}
        for r in new_doc.estadillo_rows:
            if r.col and r.col.normalized is not None and r.fil and r.fil.normalized is not None:
                c = r.col.normalized
                f = r.fil.normalized
                key = (c, f)
                seen_coords.setdefault(key, []).append(r.source_page)

        for (c, f), pages in seen_coords.items():
            if len(pages) > 1:
                added_warnings.append(
                    ExtractionWarning(
                        code="DUPLICATE_COORDINATES",
                        message=f"Coordenadas duplicadas (col={c}, fil={f}) detectadas en páginas {pages}.",
                        severity="warning",
                        source_page=pages[0],
                        field_name="col,fil",
                        details={"col": c, "fil": f, "pages": pages},
                    )
                )

    if cfg.validate_sequences:
        # Agrupar filas por columna para evaluar secuencias
        rows_by_col: dict[int, list[EstadilloRow]] = {}
        for r in new_doc.estadillo_rows:
            if r.col and r.col.normalized is not None:
                rows_by_col.setdefault(r.col.normalized, []).append(r)

        for col_val, col_rows in rows_by_col.items():
            fil_sequence = [
                (r.fil.normalized, r.source_page)
                for r in col_rows
                if r.fil and r.fil.normalized is not None
            ]
            if len(fil_sequence) >= 2:
                # Inferir sentido esperado a partir del primer paso
                expected_dir = None
                for i in range(len(fil_sequence) - 1):
                    f_curr, p_curr = fil_sequence[i]
                    f_next, p_next = fil_sequence[i + 1]
                    delta = f_next - f_curr
                    if abs(delta) > 1:
                        added_warnings.append(
                            ExtractionWarning(
                                code="SUSPICIOUS_ROW_SEQUENCE",
                                message=(
                                    f"Salto sospechoso en secuencia de filas para columna {col_val}: "
                                    f"de {f_curr} a {f_next} (delta={delta}) en pág. {p_next}."
                                ),
                                severity="warning",
                                source_page=p_next,
                                field_name="fil",
                                details={"col": col_val, "prev_fil": f_curr, "next_fil": f_next, "delta": delta},
                            )
                        )
                    elif delta in (1, -1):
                        if expected_dir is None:
                            expected_dir = delta
                        elif delta != expected_dir:
                            added_warnings.append(
                                ExtractionWarning(
                                    code="SUSPICIOUS_ROW_SEQUENCE",
                                    message=(
                                        f"Inversión de sentido en secuencia de filas para columna {col_val}: "
                                        f"esperado {expected_dir:+d}, observado {delta:+d} (de {f_curr} a {f_next}) en pág. {p_next}."
                                    ),
                                    severity="warning",
                                    source_page=p_next,
                                    field_name="fil",
                                    details={"col": col_val, "expected_direction": expected_dir, "observed_delta": delta},
                                )
                            )

    if cfg.validate_bbch:
        for r in new_doc.estadillo_rows:
            if r.bbch and (r.bbch.raw or r.bbch.normalized):
                bbch_val = str(r.bbch.normalized or r.bbch.raw).strip()
                if not _BBCH_CANONICAL_RE.match(bbch_val):
                    added_warnings.append(
                        ExtractionWarning(
                            code="INVALID_BBCH_FORMAT",
                            message=f"Formato BBCH no canónico {bbch_val!r} en pág. {r.source_page} (esperado 2 dígitos 00..99).",
                            severity="warning",
                            source_page=r.source_page,
                            field_name="bbch",
                            details={"raw_bbch": bbch_val},
                        )
                    )

    if cfg.validate_heights:
        for r in new_doc.estadillo_rows:
            if r.altura_cm and r.altura_cm.normalized is not None:
                h_val = r.altura_cm.normalized
                if not math.isfinite(h_val) or h_val <= cfg.min_height_cm or h_val > cfg.max_height_cm:
                    added_warnings.append(
                        ExtractionWarning(
                            code="SUSPICIOUS_HEIGHT",
                            message=(
                                f"Altura sospechosa o fuera de rango {h_val} cm en pág. {r.source_page} "
                                f"(rango esperado: >{cfg.min_height_cm} y <={cfg.max_height_cm} cm)."
                            ),
                            severity="warning",
                            source_page=r.source_page,
                            field_name="altura_cm",
                            details={"altura_cm": h_val, "min": cfg.min_height_cm, "max": cfg.max_height_cm},
                        )
                    )

    # 3. Validación de tablas genéricas
    for tbl in new_doc.tables:
        header_len = len(tbl.headers)
        for r_idx, row in enumerate(tbl.rows):
            if header_len > 0 and len(row) != header_len:
                warn = ExtractionWarning(
                    code="IRREGULAR_TABLE_ROW",
                    message=(
                        f"Fila {r_idx} de tabla genérica '{tbl.title or 'Sin título'}' tiene {len(row)} celdas "
                        f"mientras que la cabecera define {header_len} columnas (Pág. {tbl.source_page})."
                    ),
                    severity="info",
                    source_page=tbl.source_page,
                    field_name="tables",
                    details={"table_title": tbl.title, "row_index": r_idx, "row_len": len(row), "header_len": header_len},
                )
                tbl.warnings.append(warn)
                added_warnings.append(warn)

    # 4. Validación de diagramas
    if new_doc.diagrams:
        from ..diagrams.validation import validate_diagram_ir
        for diag in new_doc.diagrams:
            diag_warns = validate_diagram_ir(diag, raise_on_error=False)
            for w in diag_warns:
                diag.warnings.append(w)
                added_warnings.append(w)

    # Consolidar y deduplicar advertencias en new_doc
    all_warns = new_doc.warnings + added_warnings
    seen_warn_keys = set()
    deduped_warns: list[ExtractionWarning] = []
    for w in all_warns:
        k = (w.code, w.source_page, w.field_name, w.message)
        if k not in seen_warn_keys:
            seen_warn_keys.add(k)
            deduped_warns.append(w)

    new_doc.warnings = deduped_warns
    return new_doc