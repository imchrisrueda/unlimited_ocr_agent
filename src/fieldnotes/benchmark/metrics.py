from typing import Optional, List, Dict, Any, Callable
from src.fieldnotes.schemas.estadillo import EstadilloDocument, EstadilloRow
from .schemas import MetricValue, CaseMetrics
from .matching import (
    MatchResult,
    match_rows_deterministic,
    get_canonical_col,
    get_canonical_fil,
    get_canonical_species,
    get_canonical_height,
    get_canonical_photo,
    get_canonical_bbch,
)


def _row_to_summary_dict(row: EstadilloRow, idx: int) -> Dict[str, Any]:
    return {
        "index": idx,
        "source_page": row.source_page,
        "col": get_canonical_col(row),
        "fil": get_canonical_fil(row),
        "especie": get_canonical_species(row),
        "altura_cm": get_canonical_height(row),
        "foto": get_canonical_photo(row),
        "bbch": get_canonical_bbch(row),
    }


def _evaluate_field_exact_lofo(
    ref_rows: List[EstadilloRow],
    pred_rows: List[EstadilloRow],
    field_name: str,
    extractor: Callable[[EstadilloRow], Any],
    total_ref: int,
    total_pred: int,
) -> MetricValue:
    """Evalúa la exactitud de un campo utilizando Leave-One-Field-Out (LOFO) matching.

    Para evitar circularidad estricta, el campo objetivo se excluye completamente del matching,
    asegurando que las correspondencias se establecen únicamente sobre la evidencia del resto
    de atributos.
    """
    matching_result = match_rows_deterministic(ref_rows, pred_rows, excluded_field=field_name)
    matched_count = len(matching_result.matched_pairs)

    counts = {
        "matched_pairs": matched_count,
        "both_present_match": 0,
        "both_present_mismatch": 0,
        "both_absent": 0,
        "ref_present_pred_absent": 0,
        "ref_absent_pred_present": 0,
        "excluded_field": field_name,
    }

    for match in matching_result.matched_pairs:
        r_val = extractor(match.ref_row)
        p_val = extractor(match.pred_row)

        if r_val is not None and p_val is not None:
            if r_val == p_val:
                counts["both_present_match"] += 1
            else:
                counts["both_present_mismatch"] += 1
        elif r_val is None and p_val is None:
            counts["both_absent"] += 1
        elif r_val is not None:
            counts["ref_present_pred_absent"] += 1
        else:
            counts["ref_absent_pred_present"] += 1

    zero_val = 1.0 if (total_ref == 0 and total_pred == 0) else 0.0
    numerator = counts["both_present_match"] + counts["both_absent"]

    return MetricValue.from_counts(
        numerator=numerator,
        denominator=matched_count,
        zero_denominator_value=zero_val,
        details=counts,
    )


def compute_case_metrics(
    case_id: str,
    category: str,
    pipeline_id: str,
    ref_doc: EstadilloDocument,
    pred_doc: EstadilloDocument,
    matching_result: Optional[MatchResult] = None,
) -> CaseMetrics:
    """Calcula deterministamente las 8 métricas obligatorias para un caso del benchmark.

    Métricas calculadas:
    1. row_precision: matched_base / total_predicted
    2. row_recall: matched_base / total_reference
    3. col_exact: aciertos en col / matched_lofo_col (LOFO col)
    4. fil_exact: aciertos en fil / matched_lofo_fil (LOFO fil)
    5. species_exact: aciertos canónicos en especie / matched_lofo_species (LOFO especie)
    6. height_exact: aciertos exactos de altura_cm / matched_lofo_height (LOFO altura_cm)
    7. photo_exact: aciertos en foto / matched_lofo_photo (LOFO foto)
    8. bbch_exact: aciertos exactos en código BBCH / matched_lofo_bbch (LOFO bbch)

    Garantías:
    - Leave-One-Field-Out (LOFO): Cada métrica de campo evalúa pares emparejados sin utilizar el propio campo evaluado.
    - Política explícita para valores ausentes:
      - Ausente/Ausente (ambos None/vacío): cuenta como coincidencia exacta (+1 acierto).
      - Presente/Ausente o Ausente/Presente: discrepancia (+0 acierto).
      - Presente/Presente: comparación canónica exacta.
    - Semántica de denominador cero:
      - Si total_predicted == 0 y total_reference == 0 -> precision = 1.0, recall = 1.0.
      - Si total_predicted == 0 y total_reference > 0 -> precision = 0.0, recall = 0.0.
      - Si matched_pairs == 0 -> métricas de campo = 1.0 si ambos docs están vacíos else 0.0.
    """
    ref_rows = [row for p in ref_doc.pages for row in p.rows]
    pred_rows = [row for p in pred_doc.pages for row in p.rows]

    total_ref = len(ref_rows)
    total_pred = len(pred_rows)

    if matching_result is None:
        matching_result = match_rows_deterministic(ref_rows, pred_rows, excluded_field=None)

    matched_count = len(matching_result.matched_pairs)
    unmatched_ref_count = len(matching_result.unmatched_reference)
    unmatched_pred_count = len(matching_result.unmatched_predicted)

    # 1. row_precision
    row_precision = MetricValue.from_counts(
        numerator=matched_count,
        denominator=total_pred,
        zero_denominator_value=1.0 if total_ref == 0 else 0.0,
        details={"matched": matched_count, "predicted": total_pred},
    )

    # 2. row_recall
    row_recall = MetricValue.from_counts(
        numerator=matched_count,
        denominator=total_ref,
        zero_denominator_value=1.0 if total_pred == 0 else 0.0,
        details={"matched": matched_count, "reference": total_ref},
    )

    # 3-8. Métricas de campo Leave-One-Field-Out (LOFO)
    col_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "col", get_canonical_col, total_ref, total_pred)
    fil_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "fil", get_canonical_fil, total_ref, total_pred)
    species_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "especie", get_canonical_species, total_ref, total_pred)
    height_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "altura_cm", get_canonical_height, total_ref, total_pred)
    photo_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "foto", get_canonical_photo, total_ref, total_pred)
    bbch_exact = _evaluate_field_exact_lofo(ref_rows, pred_rows, "bbch", get_canonical_bbch, total_ref, total_pred)

    unmatched_ref_details = [
        _row_to_summary_dict(r, idx) for idx, r in matching_result.unmatched_reference
    ]
    unmatched_pred_details = [
        _row_to_summary_dict(r, idx) for idx, r in matching_result.unmatched_predicted
    ]

    return CaseMetrics(
        case_id=case_id,
        category=category,
        pipeline_id=pipeline_id,
        total_reference_rows=total_ref,
        total_predicted_rows=total_pred,
        matched_rows=matched_count,
        unmatched_reference_rows=unmatched_ref_count,
        unmatched_predicted_rows=unmatched_pred_count,
        row_precision=row_precision,
        row_recall=row_recall,
        col_exact=col_exact,
        fil_exact=fil_exact,
        species_exact=species_exact,
        height_exact=height_exact,
        photo_exact=photo_exact,
        bbch_exact=bbch_exact,
        unmatched_reference_details=unmatched_ref_details,
        unmatched_predicted_details=unmatched_pred_details,
    )
