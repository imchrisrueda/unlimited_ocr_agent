from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Set, Dict, Any
from src.fieldnotes.schemas.estadillo import EstadilloRow
from src.fieldnotes.normalization.estadillo import _AUTHORIZED_SPECIES_MAP


def get_canonical_col(row: EstadilloRow) -> Optional[int]:
    """Extrae la columna tipada como entero canónico."""
    if row.col is not None:
        if isinstance(row.col.normalized, int):
            return row.col.normalized
        if row.col.raw is not None:
            try:
                return int(row.col.raw.strip())
            except (ValueError, TypeError):
                pass
    return None


def get_canonical_fil(row: EstadilloRow) -> Optional[int]:
    """Extrae la fila tipada como entero canónico."""
    if row.fil is not None:
        if isinstance(row.fil.normalized, int):
            return row.fil.normalized
        if row.fil.raw is not None:
            try:
                return int(row.fil.raw.strip())
            except (ValueError, TypeError):
                pass
    return None


def get_canonical_species(row: EstadilloRow) -> Optional[str]:
    """Extrae la representación canónica de especie (P, H, R, M o texto limpio)."""
    if row.especie is not None:
        if row.especie.normalized is not None:
            return str(row.especie.normalized).strip()
        if row.especie.raw is not None:
            raw_key = row.especie.raw.strip().lower()
            if raw_key in _AUTHORIZED_SPECIES_MAP:
                return _AUTHORIZED_SPECIES_MAP[raw_key]
            return row.especie.raw.strip().upper()
    return None


def get_canonical_height(row: EstadilloRow) -> Optional[float]:
    """Extrae la altura en cm como flotante canónico para comparación exacta."""
    if row.altura_cm is not None:
        if isinstance(row.altura_cm.normalized, (int, float)):
            return float(row.altura_cm.normalized)
        if row.altura_cm.raw is not None:
            try:
                return float(row.altura_cm.raw.strip().replace(",", "."))
            except (ValueError, TypeError):
                pass
    return None


def get_canonical_photo(row: EstadilloRow) -> Optional[str]:
    """Extrae el identificador o texto de fotografía."""
    if row.foto is not None:
        raw_val = (row.foto.normalized or row.foto.raw or "").strip()
        return raw_val if raw_val else None
    return None


def get_canonical_bbch(row: EstadilloRow) -> Optional[str]:
    """Extrae el código BBCH normalizado o raw exacto."""
    if row.bbch is not None:
        raw_val = (row.bbch.normalized or row.bbch.raw or "").strip()
        return raw_val if raw_val else None
    return None


def get_canonical_id(row: EstadilloRow) -> Optional[str]:
    """Extrae el identificador de registro si existe."""
    if row.id is not None:
        raw_val = (row.id.normalized or row.id.raw or "").strip()
        return raw_val if raw_val else None
    return None


def get_canonical_observaciones(row: EstadilloRow) -> Optional[str]:
    """Extrae el texto de observaciones."""
    if row.observaciones is not None:
        raw_val = (row.observaciones.normalized or row.observaciones.raw or "").strip()
        return raw_val if raw_val else None
    return None


@dataclass(frozen=True)
class RowMatch:
    """Emparejamiento uno-a-uno determinista entre una fila de referencia y una predicha."""
    ref_row: EstadilloRow
    pred_row: EstadilloRow
    ref_index: int
    pred_index: int
    score: float
    matching_fields: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchResult:
    """Resultado del emparejamiento determinista uno-a-uno."""
    matched_pairs: List[RowMatch]
    unmatched_reference: List[Tuple[int, EstadilloRow]]
    unmatched_predicted: List[Tuple[int, EstadilloRow]]


def compute_candidate_similarity(
    ref_row: EstadilloRow,
    pred_row: EstadilloRow,
    excluded_field: Optional[str] = None,
    min_similarity: float = 0.25,
) -> Tuple[float, List[str], int]:
    """Calcula deterministamente la similitud y evidencia de correspondencia entre dos filas.

    Garantías de no circularidad y rigor científico:
    1. Identidad explícita: Si ambos registros poseen 'id' no vacío, coincidencia exacta produce score 1.0;
       discrepancia explícita rechaza el emparejamiento (score 0.0).
    2. Evidencia multi-campo suficiente: Cuando no existe 'id' coincidente, se exige concordancia en AL MENOS DOS
       campos no vacíos evaluados (e.g. col y fil, o fil y especie). Filas con una única coincidencia aislada
       (como un BBCH idéntico en filas completamente distintas) se rechazan (score 0.0).
    3. Leave-One-Field-Out (LOFO): Permite excluir el campo objetivo (e.g. 'bbch', 'col', 'fil') de la función
       de matching para garantizar que la métrica de exactitud del campo se evalúe sobre correspondencias
       establecidas de forma totalmente independiente a dicho campo.
    """
    eval_fields = ["id", "col", "fil", "especie", "altura_cm", "foto", "bbch", "observaciones"]
    if excluded_field is not None:
        eval_fields = [f for f in eval_fields if f != excluded_field]

    ref_id = get_canonical_id(ref_row)
    pred_id = get_canonical_id(pred_row)

    # 1. Identidad explícita por ID
    if "id" in eval_fields and ref_id is not None and pred_id is not None:
        if ref_id == pred_id:
            page_diff = abs(ref_row.source_page - pred_row.source_page)
            page_factor = max(0.0, 1.0 - page_diff * 0.5)
            score = round(1.0 * page_factor, 6)
            return score, ["id"], 1
        else:
            return 0.0, [], 0

    # 2. Evidencia compuesta sobre atributos disponibles (sin ID explícito coincidente)
    field_extractors = {
        "col": get_canonical_col,
        "fil": get_canonical_fil,
        "especie": get_canonical_species,
        "altura_cm": get_canonical_height,
        "foto": get_canonical_photo,
        "bbch": get_canonical_bbch,
        "observaciones": get_canonical_observaciones,
    }

    matching_fields: List[str] = []
    total_relevant = 0
    matched_count = 0

    for name in eval_fields:
        if name == "id":
            continue
        extractor = field_extractors.get(name)
        if extractor is None:
            continue
        r_val = extractor(ref_row)
        p_val = extractor(pred_row)

        if r_val is not None or p_val is not None:
            total_relevant += 1
            if r_val is not None and p_val is not None:
                if r_val == p_val:
                    matched_count += 1
                    matching_fields.append(name)

    # Exigir al menos 2 campos concordantes cuando no hay ID exacto
    if matched_count < 2:
        return 0.0, [], 0

    if total_relevant == 0:
        return 0.0, [], 0

    base_score = matched_count / total_relevant

    page_diff = abs(ref_row.source_page - pred_row.source_page)
    page_factor = max(0.0, 1.0 - (page_diff * 0.5)) if page_diff > 0 else 1.0

    score = round(base_score * page_factor, 6)
    if score < min_similarity:
        return 0.0, [], 0

    return score, matching_fields, matched_count


def hungarian_max_weight(cost_matrix: List[List[int]]) -> List[Tuple[int, int]]:
    """Algoritmo Kuhn-Munkres (Húngaro) determinista para asignación máxima uno-a-uno en O(N^3).

    cost_matrix es una matriz cuadrada N x N de enteros no negativos.
    Retorna la lista de pares (row, col) que maximizan la suma total de pesos donde weight > 0.
    """
    n = len(cost_matrix)
    if n == 0:
        return []

    u = [0] * (n + 1)
    v = [0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    max_val = max(max(row) for row in cost_matrix) if cost_matrix else 0

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (n + 1)
        used = [False] * (n + 1)

        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0

            for j in range(1, n + 1):
                if not used[j]:
                    cur = (max_val - cost_matrix[i0 - 1][j - 1]) - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j

            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta

            j0 = j1
            if p[j0] == 0:
                break

        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    matches: List[Tuple[int, int]] = []
    for j in range(1, n + 1):
        if p[j] > 0:
            r = p[j] - 1
            c = j - 1
            if cost_matrix[r][c] > 0:
                matches.append((r, c))

    matches.sort(key=lambda item: (item[0], item[1]))
    return matches


def match_rows_deterministic(
    ref_rows: List[EstadilloRow],
    pred_rows: List[EstadilloRow],
    excluded_field: Optional[str] = None,
    min_similarity: float = 0.25,
) -> MatchResult:
    """Empareja deterministamente filas de referencia y predicción mediante asignación global máxima uno-a-uno.

    Garantías:
    1. Asignación global óptima: Utiliza el algoritmo Húngaro (Kuhn-Munkres O(N^3)) para maximizar la similitud global.
    2. Uno-a-uno estricto: Ninguna fila de referencia o predicción se asigna más de una vez.
    3. No circularidad: Soporta leave-one-field-out (LOFO) y rechaza emparejamientos basados en coincidencias aisladas.
    4. Desempates estables y deterministas: Ponderación jerárquica (score > num_matches > page_diff > idx_diff > ref_idx > pred_idx).
    """
    total_ref = len(ref_rows)
    total_pred = len(pred_rows)

    if total_ref == 0 and total_pred == 0:
        return MatchResult(matched_pairs=[], unmatched_reference=[], unmatched_predicted=[])

    if total_ref == 0:
        return MatchResult(
            matched_pairs=[],
            unmatched_reference=[],
            unmatched_predicted=[(j, pred_rows[j]) for j in range(total_pred)],
        )

    if total_pred == 0:
        return MatchResult(
            matched_pairs=[],
            unmatched_reference=[(i, ref_rows[i]) for i in range(total_ref)],
            unmatched_predicted=[],
        )

    N = max(total_ref, total_pred)
    W = [[0] * N for _ in range(N)]

    for i in range(total_ref):
        r = ref_rows[i]
        for j in range(total_pred):
            p = pred_rows[j]
            score, matching_fields, num_matches = compute_candidate_similarity(
                r, p, excluded_field=excluded_field, min_similarity=min_similarity
            )
            if score > 0:
                score_int = int(round(score * 1_000_000))
                page_diff = abs(r.source_page - p.source_page)
                idx_diff = abs(i - j)
                tie_bonus = (num_matches * 100_000) - (page_diff * 10_000) - (idx_diff * 10) - (i * 2) - j + 1_000_000
                W[i][j] = score_int * 10_000_000 + max(0, tie_bonus)

    raw_matches = hungarian_max_weight(W)

    matched_ref: Set[int] = set()
    matched_pred: Set[int] = set()
    matched_pairs: List[RowMatch] = []

    for i, j in raw_matches:
        if i < total_ref and j < total_pred and W[i][j] > 0:
            matched_ref.add(i)
            matched_pred.add(j)
            score, matching_fields, _ = compute_candidate_similarity(
                ref_rows[i], pred_rows[j], excluded_field=excluded_field, min_similarity=min_similarity
            )
            matched_pairs.append(RowMatch(
                ref_row=ref_rows[i],
                pred_row=pred_rows[j],
                ref_index=i,
                pred_index=j,
                score=score,
                matching_fields=matching_fields,
            ))

    matched_pairs.sort(key=lambda m: (m.ref_index, m.pred_index))

    unmatched_ref = [(i, ref_rows[i]) for i in range(total_ref) if i not in matched_ref]
    unmatched_pred = [(j, pred_rows[j]) for j in range(total_pred) if j not in matched_pred]

    return MatchResult(
        matched_pairs=matched_pairs,
        unmatched_reference=unmatched_ref,
        unmatched_predicted=unmatched_pred,
    )

