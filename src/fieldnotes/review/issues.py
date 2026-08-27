from typing import Optional, Any, Union
from src.fieldnotes.schemas.estadillo import EstadilloDocument, EstadilloRow
from src.fieldnotes.schemas.notebook import NotebookDocument, NotebookPageDTO
from src.fieldnotes.schemas.dto import (
    EstadilloPageDTO,
    validate_visual_bbox1000,
    visual_bbox1000_to_normalized,
)
from src.fieldnotes.schemas.review import (
    ReviewIssue,
    NormalizedBBox,
    generate_issue_id,
)


def derive_row_key(row: EstadilloRow) -> Optional[str]:
    """Deriva de forma conservadora y determinista una clave textual para una fila.

    Precedencia:
    1. col y fil presentes -> 'col{col}_fil{fil}'
    2. id presente -> 'id_{id}'
    3. fil presente -> 'fil{fil}'
    4. col presente -> 'col{col}'
    5. Ninguno -> None
    """
    col_val = None
    if row.col is not None:
        col_val = row.col.normalized if row.col.normalized is not None else row.col.raw

    fil_val = None
    if row.fil is not None:
        fil_val = row.fil.normalized if row.fil.normalized is not None else row.fil.raw

    if col_val is not None and fil_val is not None:
        return f"col{col_val}_fil{fil_val}"

    if row.id is not None:
        id_val = row.id.normalized if row.id.normalized is not None else row.id.raw
        if id_val is not None and str(id_val).strip():
            return f"id_{str(id_val).strip()}"

    if fil_val is not None:
        return f"fil{fil_val}"

    if col_val is not None:
        return f"col{col_val}"

    return None


def deduplicate_and_sort_issues(raw_issues: list[ReviewIssue]) -> list[ReviewIssue]:
    """Deduplica deterministamente y ordena de forma reproducible una lista de ReviewIssues."""
    deduped_issues: list[ReviewIssue] = []
    seen_keys: set[tuple[Optional[int], str, str, str, str]] = set()

    for issue in raw_issues:
        sig = (
            issue.page,
            issue.row_key or "",
            issue.field,
            issue.warning_code or "",
            issue.reason,
        )
        if sig not in seen_keys:
            seen_keys.add(sig)
            deduped_issues.append(issue)

    # Ordenación determinista y estable (page=None se ordena primero con clave 0)
    deduped_issues.sort(
        key=lambda it: (
            0 if it.page is None else it.page,
            it.row_key or "",
            it.field,
            it.warning_code or "",
            it.reason,
        )
    )
    return deduped_issues


def extract_review_issues_from_notebook_document(doc: NotebookDocument) -> list[ReviewIssue]:
    """Convierte incertidumbres y ExtractionWarnings de NotebookDocument en ReviewIssues deterministas."""
    raw_issues: list[ReviewIssue] = []

    # 1. Cabecera global
    if doc.header is not None:
        for field_name in (
            "objetivo",
            "fecha",
            "asistentes",
            "equipamiento",
            "situacion_atmosferica",
            "especies_declaradas",
        ):
            ev = getattr(doc.header, field_name)
            if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                reason = f"Valor incierto en cabecera campo '{field_name}' (raw: {ev.raw!r})"
                issue_id = generate_issue_id(
                    page=ev.source_page,
                    field=field_name,
                    row_key=None,
                    warning_code="UNCERTAIN_EVIDENCE",
                    reason=reason,
                )
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=ev.source_page,
                        field=field_name,
                        row_key=None,
                        reason=reason,
                        candidates=candidates,
                        warning_code="UNCERTAIN_EVIDENCE",
                        provenance={"source": "header_evidence", "raw": ev.raw, "normalized": ev.normalized},
                    )
                )

    # 2. Por página
    for page in doc.pages:
        if page.header is not None and doc.header is None:
            for field_name in (
                "objetivo",
                "fecha",
                "asistentes",
                "equipamiento",
                "situacion_atmosferica",
                "especies_declaradas",
            ):
                ev = getattr(page.header, field_name)
                if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                    candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                    reason = f"Valor incierto en cabecera de página campo '{field_name}' (raw: {ev.raw!r})"
                    issue_id = generate_issue_id(
                        page=ev.source_page,
                        field=field_name,
                        row_key=None,
                        warning_code="UNCERTAIN_EVIDENCE",
                        reason=reason,
                    )
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=ev.source_page,
                            field=field_name,
                            row_key=None,
                            reason=reason,
                            candidates=candidates,
                            warning_code="UNCERTAIN_EVIDENCE",
                            provenance={"source": "page_header_evidence", "raw": ev.raw, "normalized": ev.normalized},
                        )
                    )

        # Filas de estadillo
        for row in page.estadillo_rows:
            r_key = derive_row_key(row)
            for field_name in (
                "id",
                "col",
                "fil",
                "especie",
                "altura_cm",
                "foto",
                "bbch",
                "observaciones",
            ):
                ev = getattr(row, field_name)
                if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                    candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                    row_desc = f" [{r_key}]" if r_key else ""
                    reason = f"Valor incierto en fila{row_desc} campo '{field_name}' (raw: {ev.raw!r})"
                    issue_id = generate_issue_id(
                        page=row.source_page,
                        field=field_name,
                        row_key=r_key,
                        warning_code="UNCERTAIN_EVIDENCE",
                        reason=reason,
                    )
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=row.source_page,
                            field=field_name,
                            row_key=r_key,
                            reason=reason,
                            candidates=candidates,
                            warning_code="UNCERTAIN_EVIDENCE",
                            provenance={"source": "row_evidence", "raw": ev.raw, "normalized": ev.normalized},
                        )
                    )

        # Secciones
        for s_idx, sec in enumerate(page.sections):
            if sec.uncertain:
                sec_desc = f" '{sec.title}'" if sec.title else f" {s_idx}"
                reason = f"Sección{sec_desc} marcada como incierta"
                issue_id = generate_issue_id(page=sec.source_page, field="section", row_key=f"sec_{s_idx}", warning_code="UNCERTAIN_SECTION", reason=reason)
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=sec.source_page,
                        field="section",
                        row_key=f"sec_{s_idx}",
                        reason=reason,
                        warning_code="UNCERTAIN_SECTION",
                    )
                )
            for p_idx, p in enumerate(sec.paragraphs):
                if p.uncertain or bool(p.alternatives):
                    reason = f"Párrafo {p_idx} en sección '{sec.title or 'Sin título'}' marcado como incierto (raw: {p.raw!r})"
                    issue_id = generate_issue_id(page=p.source_page, field="paragraph", row_key=f"sec_{s_idx}_p{p_idx}", warning_code="UNCERTAIN_PARAGRAPH", reason=reason)
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=p.source_page,
                            field="paragraph",
                            row_key=f"sec_{s_idx}_p{p_idx}",
                            reason=reason,
                            candidates=[str(a) for a in p.alternatives] if p.alternatives else [],
                            warning_code="UNCERTAIN_PARAGRAPH",
                        )
                    )

        # Notas
        for n_idx, note in enumerate(page.notes):
            if note.uncertain or note.text.uncertain or bool(note.text.alternatives):
                reason = f"Nota {n_idx} marcada como incierta (raw: {note.text.raw!r})"
                issue_id = generate_issue_id(page=note.source_page, field="note", row_key=f"note_{n_idx}", warning_code="UNCERTAIN_NOTE", reason=reason)
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=note.source_page,
                        field="note",
                        row_key=f"note_{n_idx}",
                        reason=reason,
                        candidates=[str(a) for a in note.text.alternatives] if note.text.alternatives else [],
                        warning_code="UNCERTAIN_NOTE",
                    )
                )

        # Tablas genéricas
        for t_idx, tbl in enumerate(page.tables):
            if tbl.uncertain:
                tbl_title = f" '{tbl.title}'" if tbl.title else f" {t_idx}"
                reason = f"Tabla genérica{tbl_title} contiene celdas o estructura incierta"
                issue_id = generate_issue_id(page=tbl.source_page, field="table", row_key=f"tbl_{t_idx}", warning_code="UNCERTAIN_TABLE", reason=reason)
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=tbl.source_page,
                        field="table",
                        row_key=f"tbl_{t_idx}",
                        reason=reason,
                        warning_code="UNCERTAIN_TABLE",
                    )
                )
            for r_idx, r_cells in enumerate(tbl.rows):
                for c_idx, c_val in enumerate(r_cells):
                    if c_val.uncertain or bool(c_val.alternatives):
                        reason = f"Celda [{r_idx}, {c_idx}] en tabla '{tbl.title or 'Sin título'}' incierta (raw: {c_val.raw!r})"
                        issue_id = generate_issue_id(page=c_val.source_page, field=f"cell_{r_idx}_{c_idx}", row_key=f"tbl_{t_idx}_r{r_idx}_c{c_idx}", warning_code="UNCERTAIN_CELL", reason=reason)
                        raw_issues.append(
                            ReviewIssue(
                                issue_id=issue_id,
                                page=c_val.source_page,
                                field=f"cell_{r_idx}_{c_idx}",
                                row_key=f"tbl_{t_idx}_r{r_idx}_c{c_idx}",
                                reason=reason,
                                candidates=[str(a) for a in c_val.alternatives] if c_val.alternatives else [],
                                warning_code="UNCERTAIN_CELL",
                            )
                        )

        # Diagramas
        for d_idx, diag in enumerate(page.diagrams):
            if diag.uncertain:
                reason = f"Diagrama {diag.diagram_type} ({diag.title or f'diag_{d_idx}'}) marcado como incierto"
                issue_id = generate_issue_id(page=diag.source_page, field="diagram", row_key=f"diag_{d_idx}", warning_code="UNCERTAIN_DIAGRAM", reason=reason)
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=diag.source_page,
                        field="diagram",
                        row_key=f"diag_{d_idx}",
                        reason=reason,
                        warning_code="UNCERTAIN_DIAGRAM",
                    )
                )
            for gc in diag.geographic_coordinates:
                if gc.uncertain or bool(gc.alternatives):
                    reason = f"Coordenada GPS '{gc.id}' en diagrama incierta (raw: {gc.raw_text!r})"
                    issue_id = generate_issue_id(page=gc.source_page, field="geographic_coordinate", row_key=gc.id, warning_code="UNCERTAIN_GPS", reason=reason)
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=gc.source_page,
                            field="geographic_coordinate",
                            row_key=gc.id,
                            reason=reason,
                            candidates=[str(a) for a in gc.alternatives] if gc.alternatives else [],
                            warning_code="UNCERTAIN_GPS",
                        )
                    )

    # 3. Warnings globales
    for warning in doc.warnings:
        page = warning.source_page
        field = warning.field_name if warning.field_name is not None else "document"
        warning_code = warning.code
        reason = warning.message

        r_key: Optional[str] = None
        candidates: list[str] = []

        if warning.details:
            if "col" in warning.details and "fil" in warning.details:
                c = warning.details["col"]
                f = warning.details["fil"]
                if c is not None and f is not None:
                    r_key = f"col{c}_fil{f}"
            elif "row_id" in warning.details and warning.details["row_id"]:
                r_key = f"id_{warning.details['row_id']}"
            elif "occurrences" in warning.details and isinstance(warning.details["occurrences"], list):
                occs = warning.details["occurrences"]
                if occs and isinstance(occs[0], dict) and occs[0].get("id"):
                    r_key = f"id_{occs[0]['id']}"

            if "candidates" in warning.details and isinstance(warning.details["candidates"], list):
                candidates = [str(c) for c in warning.details["candidates"]]

        issue_id = generate_issue_id(
            page=page,
            field=field,
            row_key=r_key,
            warning_code=warning_code,
            reason=reason,
        )
        provenance = {"severity": warning.severity}
        if warning.details:
            provenance["details"] = warning.details

        raw_issues.append(
            ReviewIssue(
                issue_id=issue_id,
                page=page,
                field=field,
                row_key=r_key,
                reason=reason,
                candidates=candidates,
                warning_code=warning_code,
                provenance=provenance,
            )
        )

    # 4. Warnings por página
    for page in doc.pages:
        for warning in page.warnings:
            page_num = warning.source_page if warning.source_page is not None else page.page_number
            field = warning.field_name if warning.field_name is not None else "page"
            warning_code = warning.code
            reason = warning.message
            r_key = None
            if warning.details and "col" in warning.details and "fil" in warning.details:
                r_key = f"col{warning.details['col']}_fil{warning.details['fil']}"

            issue_id = generate_issue_id(
                page=page_num,
                field=field,
                row_key=r_key,
                warning_code=warning_code,
                reason=reason,
            )
            provenance = {"severity": warning.severity}
            if warning.details:
                provenance["details"] = warning.details

            raw_issues.append(
                ReviewIssue(
                    issue_id=issue_id,
                    page=page_num,
                    field=field,
                    row_key=r_key,
                    reason=reason,
                    candidates=[],
                    warning_code=warning_code,
                    provenance=provenance,
                )
            )

    return deduplicate_and_sort_issues(raw_issues)


def extract_review_issues_from_document(
    doc: Union[EstadilloDocument, NotebookDocument],
) -> list[ReviewIssue]:
    """Convierte incertidumbres y ExtractionWarnings de EstadilloDocument o NotebookDocument en ReviewIssues deterministas.

    Función pura, no mutativa, determinista, deduplicada y ordenada.
    Preserva page=None para incidencias documentales globales sin inventar un número de página arbitrario.
    """
    if isinstance(doc, NotebookDocument):
        return extract_review_issues_from_notebook_document(doc)

    raw_issues: list[ReviewIssue] = []

    # 1. Extraer incertidumbres de EvidenceValue en cabecera global
    if doc.header is not None:
        for field_name in (
            "objetivo",
            "fecha",
            "asistentes",
            "equipamiento",
            "situacion_atmosferica",
            "especies_declaradas",
        ):
            ev = getattr(doc.header, field_name)
            if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                reason = f"Valor incierto en cabecera campo '{field_name}' (raw: {ev.raw!r})"
                issue_id = generate_issue_id(
                    page=ev.source_page,
                    field=field_name,
                    row_key=None,
                    warning_code="UNCERTAIN_EVIDENCE",
                    reason=reason,
                )
                raw_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=ev.source_page,
                        field=field_name,
                        row_key=None,
                        reason=reason,
                        candidates=candidates,
                        warning_code="UNCERTAIN_EVIDENCE",
                        provenance={"source": "header_evidence", "raw": ev.raw, "normalized": ev.normalized},
                    )
                )

    # 2. Extraer incertidumbres de EvidenceValue en páginas y filas
    for page in doc.pages:
        # Cabecera por página si existe y no está en cabecera global
        if page.header is not None and doc.header is None:
            for field_name in (
                "objetivo",
                "fecha",
                "asistentes",
                "equipamiento",
                "situacion_atmosferica",
                "especies_declaradas",
            ):
                ev = getattr(page.header, field_name)
                if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                    candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                    reason = f"Valor incierto en cabecera de página campo '{field_name}' (raw: {ev.raw!r})"
                    issue_id = generate_issue_id(
                        page=ev.source_page,
                        field=field_name,
                        row_key=None,
                        warning_code="UNCERTAIN_EVIDENCE",
                        reason=reason,
                    )
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=ev.source_page,
                            field=field_name,
                            row_key=None,
                            reason=reason,
                            candidates=candidates,
                            warning_code="UNCERTAIN_EVIDENCE",
                            provenance={"source": "page_header_evidence", "raw": ev.raw, "normalized": ev.normalized},
                        )
                    )

        # Filas de la página
        for row in page.rows:
            r_key = derive_row_key(row)
            for field_name in (
                "id",
                "col",
                "fil",
                "especie",
                "altura_cm",
                "foto",
                "bbch",
                "observaciones",
            ):
                ev = getattr(row, field_name)
                if ev is not None and (ev.uncertain or bool(ev.alternatives)):
                    candidates = [str(a) for a in ev.alternatives] if ev.alternatives else []
                    row_desc = f" [{r_key}]" if r_key else ""
                    reason = f"Valor incierto en fila{row_desc} campo '{field_name}' (raw: {ev.raw!r})"
                    issue_id = generate_issue_id(
                        page=row.source_page,
                        field=field_name,
                        row_key=r_key,
                        warning_code="UNCERTAIN_EVIDENCE",
                        reason=reason,
                    )
                    raw_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=row.source_page,
                            field=field_name,
                            row_key=r_key,
                            reason=reason,
                            candidates=candidates,
                            warning_code="UNCERTAIN_EVIDENCE",
                            provenance={"source": "row_evidence", "raw": ev.raw, "normalized": ev.normalized},
                        )
                    )

    # 3. Extraer issues desde ExtractionWarnings globales del documento
    for warning in doc.warnings:
        # Si source_page es None, se preserva None sin inventar page=1
        page = warning.source_page
        field = warning.field_name if warning.field_name is not None else "document"
        warning_code = warning.code
        reason = warning.message

        r_key: Optional[str] = None
        candidates: list[str] = []

        if warning.details:
            if "col" in warning.details and "fil" in warning.details:
                c = warning.details["col"]
                f = warning.details["fil"]
                if c is not None and f is not None:
                    r_key = f"col{c}_fil{f}"
            elif "row_id" in warning.details and warning.details["row_id"]:
                r_key = f"id_{warning.details['row_id']}"
            elif "occurrences" in warning.details and isinstance(warning.details["occurrences"], list):
                occs = warning.details["occurrences"]
                if occs and isinstance(occs[0], dict) and occs[0].get("id"):
                    r_key = f"id_{occs[0]['id']}"

            if "candidates" in warning.details and isinstance(warning.details["candidates"], list):
                candidates = [str(c) for c in warning.details["candidates"]]

        issue_id = generate_issue_id(
            page=page,
            field=field,
            row_key=r_key,
            warning_code=warning_code,
            reason=reason,
        )
        provenance = {"severity": warning.severity}
        if warning.details:
            provenance["details"] = warning.details

        raw_issues.append(
            ReviewIssue(
                issue_id=issue_id,
                page=page,
                field=field,
                row_key=r_key,
                reason=reason,
                candidates=candidates,
                warning_code=warning_code,
                provenance=provenance,
            )
        )

    # 4. Extraer issues desde ExtractionWarnings a nivel de página (si no están ya en doc.warnings)
    for page in doc.pages:
        for warning in page.warnings:
            page_num = warning.source_page if warning.source_page is not None else page.page_number
            field = warning.field_name if warning.field_name is not None else "page"
            warning_code = warning.code
            reason = warning.message
            r_key = None
            if warning.details and "col" in warning.details and "fil" in warning.details:
                r_key = f"col{warning.details['col']}_fil{warning.details['fil']}"

            issue_id = generate_issue_id(
                page=page_num,
                field=field,
                row_key=r_key,
                warning_code=warning_code,
                reason=reason,
            )
            provenance = {"severity": warning.severity}
            if warning.details:
                provenance["details"] = warning.details

            raw_issues.append(
                ReviewIssue(
                    issue_id=issue_id,
                    page=page_num,
                    field=field,
                    row_key=r_key,
                    reason=reason,
                    candidates=[],
                    warning_code=warning_code,
                    provenance=provenance,
                )
            )

    return deduplicate_and_sort_issues(raw_issues)


def extract_invalid_region_issues(
    page_dtos: list[Union[EstadilloPageDTO, NotebookPageDTO]],
) -> list[ReviewIssue]:
    """Genera ReviewIssues auditables con código INVALID_VISUAL_REGION para regiones descartadas.

    Función pura, no mutativa y determinista. Registra el descarte de coordenadas inválidas
    (fuera de [0,1000], invertidas, degeneradas o con discrepancia de página) sin asociarles crop.
    """
    invalid_issues: list[ReviewIssue] = []

    for dto in page_dtos:
        page_num = dto.page_number

        # 1. Regiones explícitas de página
        for idx, reg in enumerate(dto.regions):
            if reg.page is not None and reg.page != page_num:
                reason = f"Región visual en índice {idx} descartada por discrepancia de página (esperada={page_num}, recibida={reg.page})"
                issue_id = generate_issue_id(page=page_num, field=reg.field or "region", row_key=reg.row_key, warning_code="INVALID_VISUAL_REGION", reason=reason)
                invalid_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=page_num,
                        field=reg.field or "region",
                        row_key=reg.row_key,
                        reason=reason,
                        warning_code="INVALID_VISUAL_REGION",
                        crop_path=None,
                    )
                )
                continue

            if reg.bbox is not None and validate_visual_bbox1000(reg.bbox) is None:
                reason = f"Región visual en índice {idx} descartada por geometría o límites inválidos (x0={reg.bbox.x0}, y0={reg.bbox.y0}, x1={reg.bbox.x1}, y1={reg.bbox.y1})"
                issue_id = generate_issue_id(page=page_num, field=reg.field or "region", row_key=reg.row_key, warning_code="INVALID_VISUAL_REGION", reason=reason)
                invalid_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=page_num,
                        field=reg.field or "region",
                        row_key=reg.row_key,
                        reason=reason,
                        warning_code="INVALID_VISUAL_REGION",
                        crop_path=None,
                    )
                )

        # 2. Regiones de cabecera
        if dto.header is not None and dto.header.field_bboxes:
            for field_name, f_box in dto.header.field_bboxes.items():
                if f_box is not None and validate_visual_bbox1000(f_box) is None:
                    reason = f"Bounding box de cabecera campo '{field_name}' descartado por geometría o límites inválidos (x0={f_box.x0}, y0={f_box.y0}, x1={f_box.x1}, y1={f_box.y1})"
                    issue_id = generate_issue_id(page=page_num, field=field_name, row_key=None, warning_code="INVALID_VISUAL_REGION", reason=reason)
                    invalid_issues.append(
                        ReviewIssue(
                            issue_id=issue_id,
                            page=page_num,
                            field=field_name,
                            row_key=None,
                            reason=reason,
                            warning_code="INVALID_VISUAL_REGION",
                            crop_path=None,
                        )
                    )

        # 3. Regiones de filas
        current_col = None
        rows = getattr(dto, "estadillo_rows", None) or getattr(dto, "rows", [])
        for idx, row_dto in enumerate(rows):
            if row_dto.col is not None:
                current_col = row_dto.col

            r_key = None
            if current_col is not None and row_dto.fil is not None:
                r_key = f"col{current_col}_fil{row_dto.fil}"
            elif row_dto.id is not None:
                r_key = f"id_{row_dto.id}"
            elif row_dto.fil is not None:
                r_key = f"fil{row_dto.fil}"

            if row_dto.bbox is not None and validate_visual_bbox1000(row_dto.bbox) is None:
                reason = f"Bounding box de fila descartado por geometría o límites inválidos (x0={row_dto.bbox.x0}, y0={row_dto.bbox.y0}, x1={row_dto.bbox.x1}, y1={row_dto.bbox.y1})"
                issue_id = generate_issue_id(page=page_num, field="bbox", row_key=r_key, warning_code="INVALID_VISUAL_REGION", reason=reason)
                invalid_issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        page=page_num,
                        field="bbox",
                        row_key=r_key,
                        reason=reason,
                        warning_code="INVALID_VISUAL_REGION",
                        crop_path=None,
                    )
                )

            if row_dto.field_bboxes:
                for field_name, f_box in row_dto.field_bboxes.items():
                    if f_box is not None and validate_visual_bbox1000(f_box) is None:
                        reason = f"Bounding box de celda '{field_name}' descartado por geometría o límites inválidos (x0={f_box.x0}, y0={f_box.y0}, x1={f_box.x1}, y1={f_box.y1})"
                        issue_id = generate_issue_id(page=page_num, field=field_name, row_key=r_key, warning_code="INVALID_VISUAL_REGION", reason=reason)
                        invalid_issues.append(
                            ReviewIssue(
                                issue_id=issue_id,
                                page=page_num,
                                field=field_name,
                                row_key=r_key,
                                reason=reason,
                                warning_code="INVALID_VISUAL_REGION",
                                crop_path=None,
                            )
                        )

    return deduplicate_and_sort_issues(invalid_issues)


def collect_dto_regions(
    page_dtos: list[Union[EstadilloPageDTO, NotebookPageDTO]],
) -> dict[tuple[int, Optional[str], Optional[str]], NormalizedBBox]:
    """Recolecta y valida todas las regiones visuales explícitas contenidas en los DTOs de extracción.

    Mapea (page_number, row_key, field_name) -> NormalizedBBox.
    Valida individualmente cada bounding box candidato y DESCARTA de forma pura y determinista
    cualquier región con coordenadas fuera de [0,1000], invertidas, degeneradas o con
    discrepancia de página, sin interrumpir la extracción ni invalidar otras regiones válidas.
    """
    regions_map: dict[tuple[int, Optional[str], Optional[str]], NormalizedBBox] = {}

    for dto in page_dtos:
        page_num = dto.page_number

        # 1. Regiones explícitas de página
        for reg in dto.regions:
            if reg.page is not None and reg.page != page_num:
                continue  # ignorar discrepancia de página
            valid_box = validate_visual_bbox1000(reg.bbox)
            if valid_box is not None:
                norm_bbox = visual_bbox1000_to_normalized(valid_box)
                # Registro por campo y fila
                regions_map[(page_num, reg.row_key, reg.field)] = norm_bbox
                if reg.field and reg.row_key is None:
                    regions_map[(page_num, None, reg.field)] = norm_bbox

        # 2. Regiones de cabecera
        if dto.header is not None and dto.header.field_bboxes:
            for field_name, candidate_box in dto.header.field_bboxes.items():
                valid_box = validate_visual_bbox1000(candidate_box)
                if valid_box is not None:
                    regions_map[(page_num, None, field_name)] = visual_bbox1000_to_normalized(valid_box)

        # 3. Regiones de filas
        current_col = None
        rows = getattr(dto, "estadillo_rows", None) or getattr(dto, "rows", [])
        for idx, row_dto in enumerate(rows):
            if row_dto.col is not None:
                current_col = row_dto.col

            # Derivar claves posibles para la fila
            keys_for_row: list[str] = []
            if current_col is not None and row_dto.fil is not None:
                keys_for_row.append(f"col{current_col}_fil{row_dto.fil}")
            if row_dto.col is not None and row_dto.fil is not None:
                k = f"col{row_dto.col}_fil{row_dto.fil}"
                if k not in keys_for_row:
                    keys_for_row.append(k)
            if row_dto.id is not None:
                keys_for_row.append(f"id_{row_dto.id}")
            if row_dto.fil is not None:
                keys_for_row.append(f"fil{row_dto.fil}")
            if row_dto.col is not None:
                keys_for_row.append(f"col{row_dto.col}")

            valid_row_box = validate_visual_bbox1000(row_dto.bbox)
            if valid_row_box is not None:
                norm_row_bbox = visual_bbox1000_to_normalized(valid_row_box)
                for r_key in keys_for_row:
                    regions_map[(page_num, r_key, "*")] = norm_row_bbox
                    regions_map[(page_num, r_key, None)] = norm_row_bbox
                regions_map[(page_num, f"idx_{idx}", "*")] = norm_row_bbox

            if row_dto.field_bboxes:
                for field_name, f_candidate in row_dto.field_bboxes.items():
                    valid_fb = validate_visual_bbox1000(f_candidate)
                    if valid_fb is not None:
                        norm_fb = visual_bbox1000_to_normalized(valid_fb)
                        for r_key in keys_for_row:
                            regions_map[(page_num, r_key, field_name)] = norm_fb
                        regions_map[(page_num, f"idx_{idx}", field_name)] = norm_fb

    return regions_map


def match_region_for_issue(
    issue: ReviewIssue,
    regions_map: dict[tuple[int, Optional[str], Optional[str]], NormalizedBBox],
) -> Optional[NormalizedBBox]:
    """Localiza la región visual correspondiente a un ReviewIssue en el mapa de regiones.

    Búsqueda ordenada de mayor a menor especificidad:
    1. (page, row_key, field)
    2. Subcampos si field es múltiple (ej. 'col,fil' -> 'col' o 'fil')
    3. (page, row_key, '*') o (page, row_key, None)
    4. (page, None, field) solo para campos de cabecera/página sin row_key.
    No asocia warnings documentales (page=None), ni warnings de descarte (INVALID_VISUAL_REGION),
    ni asigna filas a warnings globales.
    """
    if issue.page is None:
        return None

    # Regiones descartadas por inválidas nunca generan crop
    if issue.warning_code == "INVALID_VISUAL_REGION":
        return None

    # 1. Coincidencia exacta
    exact_key = (issue.page, issue.row_key, issue.field)
    if exact_key in regions_map:
        return regions_map[exact_key]

    # 2. Subcampos si el campo contiene coma (ej: 'col,fil')
    if "," in issue.field and issue.row_key:
        for subfield in issue.field.split(","):
            sub_key = (issue.page, issue.row_key, subfield.strip())
            if sub_key in regions_map:
                return regions_map[sub_key]

    # 3. Bounding box de la fila completa si el issue tiene row_key
    if issue.row_key:
        row_star_key = (issue.page, issue.row_key, "*")
        if row_star_key in regions_map:
            return regions_map[row_star_key]
        row_none_key = (issue.page, issue.row_key, None)
        if row_none_key in regions_map:
            return regions_map[row_none_key]

    # 4. Campo a nivel de página/cabecera únicamente si no pertenece a una fila
    if issue.row_key is None:
        field_page_key = (issue.page, None, issue.field)
        if field_page_key in regions_map:
            return regions_map[field_page_key]

    return None