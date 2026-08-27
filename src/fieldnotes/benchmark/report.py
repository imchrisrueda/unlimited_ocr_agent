import json
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from src.fieldnotes.schemas.estadillo import EstadilloDocument
from src.fieldnotes.profiles.estadillo import write_atomic_file
from .schemas import (
    MetricValue,
    CaseMetrics,
    AggregatedMetrics,
    PipelineConfig,
    BenchmarkCase,
    BenchmarkManifest,
    GroundTruthDocument,
    BenchmarkPrediction,
    BenchmarkReport,
    validate_confined_relative_path,
)
from .metrics import compute_case_metrics


def _compute_macro_metric(values: List[float], total_cases: int) -> MetricValue:
    """Calcula la media aritmética macro no ponderada sobre los valores de los casos."""
    if not values:
        return MetricValue.from_counts(0, 0, zero_denominator_value=1.0)
    mean_val = round(sum(values) / len(values), 6)
    return MetricValue(
        numerator=int(round(mean_val * 1000000)),
        denominator=1000000,
        value=mean_val,
        details={"case_count": len(values), "total_cases": total_cases},
    )


def aggregate_metrics(
    case_metrics_list: List[CaseMetrics],
    pipeline_id: str,
    category: Optional[str] = None,
) -> AggregatedMetrics:
    """Calcula deterministamente agregaciones micro y macro para un conjunto de casos."""
    total_cases = len(case_metrics_list)
    total_ref = sum(c.total_reference_rows for c in case_metrics_list)
    total_pred = sum(c.total_predicted_rows for c in case_metrics_list)
    total_matched = sum(c.matched_rows for c in case_metrics_list)
    total_un_ref = sum(c.unmatched_reference_rows for c in case_metrics_list)
    total_un_pred = sum(c.unmatched_predicted_rows for c in case_metrics_list)

    zero_field_val = 1.0 if (total_ref == 0 and total_pred == 0) else 0.0

    # Micro agregaciones
    row_prec_micro = MetricValue.from_counts(
        numerator=sum(c.row_precision.numerator for c in case_metrics_list),
        denominator=sum(c.row_precision.denominator for c in case_metrics_list),
        zero_denominator_value=1.0 if total_ref == 0 else 0.0,
    )
    row_rec_micro = MetricValue.from_counts(
        numerator=sum(c.row_recall.numerator for c in case_metrics_list),
        denominator=sum(c.row_recall.denominator for c in case_metrics_list),
        zero_denominator_value=1.0 if total_pred == 0 else 0.0,
    )
    col_exact_micro = MetricValue.from_counts(
        numerator=sum(c.col_exact.numerator for c in case_metrics_list),
        denominator=sum(c.col_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )
    fil_exact_micro = MetricValue.from_counts(
        numerator=sum(c.fil_exact.numerator for c in case_metrics_list),
        denominator=sum(c.fil_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )
    spe_exact_micro = MetricValue.from_counts(
        numerator=sum(c.species_exact.numerator for c in case_metrics_list),
        denominator=sum(c.species_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )
    hei_exact_micro = MetricValue.from_counts(
        numerator=sum(c.height_exact.numerator for c in case_metrics_list),
        denominator=sum(c.height_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )
    pho_exact_micro = MetricValue.from_counts(
        numerator=sum(c.photo_exact.numerator for c in case_metrics_list),
        denominator=sum(c.photo_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )
    bbch_exact_micro = MetricValue.from_counts(
        numerator=sum(c.bbch_exact.numerator for c in case_metrics_list),
        denominator=sum(c.bbch_exact.denominator for c in case_metrics_list),
        zero_denominator_value=zero_field_val,
    )

    # Macro agregaciones (medias de valores por caso)
    row_prec_macro = _compute_macro_metric([c.row_precision.value for c in case_metrics_list], total_cases)
    row_rec_macro = _compute_macro_metric([c.row_recall.value for c in case_metrics_list], total_cases)
    col_exact_macro = _compute_macro_metric([c.col_exact.value for c in case_metrics_list], total_cases)
    fil_exact_macro = _compute_macro_metric([c.fil_exact.value for c in case_metrics_list], total_cases)
    spe_exact_macro = _compute_macro_metric([c.species_exact.value for c in case_metrics_list], total_cases)
    hei_exact_macro = _compute_macro_metric([c.height_exact.value for c in case_metrics_list], total_cases)
    pho_exact_macro = _compute_macro_metric([c.photo_exact.value for c in case_metrics_list], total_cases)
    bbch_exact_macro = _compute_macro_metric([c.bbch_exact.value for c in case_metrics_list], total_cases)

    return AggregatedMetrics(
        pipeline_id=pipeline_id,
        category=category,
        total_cases=total_cases,
        total_reference_rows=total_ref,
        total_predicted_rows=total_pred,
        total_matched_rows=total_matched,
        total_unmatched_reference_rows=total_un_ref,
        total_unmatched_predicted_rows=total_un_pred,
        row_precision_micro=row_prec_micro,
        row_recall_micro=row_rec_micro,
        col_exact_micro=col_exact_micro,
        fil_exact_micro=fil_exact_micro,
        species_exact_micro=spe_exact_micro,
        height_exact_micro=hei_exact_micro,
        photo_exact_micro=pho_exact_micro,
        bbch_exact_micro=bbch_exact_micro,
        row_precision_macro=row_prec_macro,
        row_recall_macro=row_rec_macro,
        col_exact_macro=col_exact_macro,
        fil_exact_macro=fil_exact_macro,
        species_exact_macro=spe_exact_macro,
        height_exact_macro=hei_exact_macro,
        photo_exact_macro=pho_exact_macro,
        bbch_exact_macro=bbch_exact_macro,
    )


def _load_ground_truth_doc(gt_path: Path, expected_case_id: str) -> EstadilloDocument:
    """Carga y valida estrictamente un archivo de verdad de referencia cruzando case_id."""
    content = gt_path.read_text(encoding="utf-8")
    data = json.loads(content)
    if "document" in data:
        gt_obj = GroundTruthDocument.model_validate(data)
        if gt_obj.case_id != expected_case_id:
            raise ValueError(
                f"Ground truth case_id mismatch: esperado '{expected_case_id}', "
                f"encontrado '{gt_obj.case_id}' en {gt_path}"
            )
        return gt_obj.document

    if "case_id" in data and data["case_id"] != expected_case_id:
        raise ValueError(
            f"Ground truth case_id mismatch: esperado '{expected_case_id}', "
            f"encontrado '{data['case_id']}' en {gt_path}"
        )
    return EstadilloDocument.model_validate(data)


def _load_prediction_doc(
    pred_path: Path, expected_case_id: str, expected_pipeline_id: str
) -> EstadilloDocument:
    """Carga y valida estrictamente un archivo de predicción cruzando case_id y pipeline_id."""
    content = pred_path.read_text(encoding="utf-8")
    data = json.loads(content)
    if "document" in data:
        pred_obj = BenchmarkPrediction.model_validate(data)
        if pred_obj.case_id != expected_case_id:
            raise ValueError(
                f"Prediction case_id mismatch: esperado '{expected_case_id}', "
                f"encontrado '{pred_obj.case_id}' en {pred_path}"
            )
        if pred_obj.pipeline_id != expected_pipeline_id:
            raise ValueError(
                f"Prediction pipeline_id mismatch: esperado '{expected_pipeline_id}', "
                f"encontrado '{pred_obj.pipeline_id}' en {pred_path}"
            )
        return pred_obj.document

    if "case_id" in data and data["case_id"] != expected_case_id:
        raise ValueError(
            f"Prediction case_id mismatch: esperado '{expected_case_id}', "
            f"encontrado '{data['case_id']}' en {pred_path}"
        )
    if "pipeline_id" in data and data["pipeline_id"] != expected_pipeline_id:
        raise ValueError(
            f"Prediction pipeline_id mismatch: esperado '{expected_pipeline_id}', "
            f"encontrado '{data['pipeline_id']}' en {pred_path}"
        )
    return EstadilloDocument.model_validate(data)


def generate_benchmark_report(
    manifest: BenchmarkManifest,
    dataset_dir: Path,
    pipelines: Optional[List[str]] = None,
    fixed_timestamp: Optional[str] = None,
) -> BenchmarkReport:
    """Genera deterministamente el informe completo del benchmark para el manifiesto y dataset dados."""
    dataset_dir = dataset_dir.resolve()
    manifest_pipe_ids = {p.pipeline_id for p in manifest.pipelines}

    if pipelines is not None:
        if len(pipelines) == 0:
            raise ValueError("No se han especificado pipelines válidos; selección vacía no permitida")
        for req_p in pipelines:
            if req_p not in manifest_pipe_ids:
                raise ValueError(f"Pipeline solicitado '{req_p}' no existe en el manifiesto")
        target_pipelines = list(pipelines)
    else:
        target_pipelines = [p.pipeline_id for p in manifest.pipelines]

    # Ordenar deterministamente casos y pipelines
    sorted_cases = sorted(manifest.cases, key=lambda c: c.case_id)
    sorted_pipelines = sorted(manifest.pipelines, key=lambda p: p.pipeline_id)
    active_pipelines = [p for p in sorted_pipelines if p.pipeline_id in target_pipelines]

    if not active_pipelines:
        raise ValueError("Ningún pipeline activo seleccionado para evaluación")

    evaluable_cases = [c for c in sorted_cases if c.evaluate_structured]
    excluded_cases = [c for c in sorted_cases if not c.evaluate_structured]

    excluded_summary = [
        {
            "case_id": c.case_id,
            "category": c.category,
            "description": c.description or "",
            "reason": "catalogued_only_no_structured_evaluation",
        }
        for c in excluded_cases
    ]

    case_results: List[CaseMetrics] = []

    for case in evaluable_cases:
        # Validar que las predicciones del caso pertenecen a pipelines del manifiesto
        for pred_pipe_key in case.predictions.keys():
            if pred_pipe_key not in manifest_pipe_ids:
                raise ValueError(
                    f"El caso '{case.case_id}' contiene una predicción para un pipeline no registrado: '{pred_pipe_key}'"
                )

        if not case.ground_truth_path:
            raise ValueError(f"El caso evaluable '{case.case_id}' requiere 'ground_truth_path'")

        gt_path = validate_confined_relative_path(case.ground_truth_path, dataset_dir)
        if not gt_path.is_file():
            raise FileNotFoundError(f"Archivo de ground truth no encontrado: {gt_path}")
        ref_doc = _load_ground_truth_doc(gt_path, expected_case_id=case.case_id)

        for pipe in active_pipelines:
            pipe_id = pipe.pipeline_id
            if pipe_id not in case.predictions:
                raise ValueError(
                    f"El caso '{case.case_id}' no contiene predicción para el pipeline '{pipe_id}'"
                )
            pred_rel_path = case.predictions[pipe_id]
            pred_path = validate_confined_relative_path(pred_rel_path, dataset_dir)
            if not pred_path.is_file():
                raise FileNotFoundError(
                    f"Archivo de predicción no encontrado para '{pipe_id}' en caso '{case.case_id}': {pred_path}"
                )
            pred_doc = _load_prediction_doc(pred_path, expected_case_id=case.case_id, expected_pipeline_id=pipe_id)

            metrics = compute_case_metrics(
                case_id=case.case_id,
                category=case.category,
                pipeline_id=pipe_id,
                ref_doc=ref_doc,
                pred_doc=pred_doc,
            )
            case_results.append(metrics)

    # Agrupaciones por categoría y globales únicamente para casos evaluables
    categories = sorted(list({c.category for c in evaluable_cases}))
    category_aggregations: List[AggregatedMetrics] = []
    global_aggregations: List[AggregatedMetrics] = []

    for pipe in active_pipelines:
        pipe_id = pipe.pipeline_id
        pipe_cases = [c for c in case_results if c.pipeline_id == pipe_id]

        for cat in categories:
            cat_cases = [c for c in pipe_cases if c.category == cat]
            if cat_cases:
                cat_agg = aggregate_metrics(cat_cases, pipeline_id=pipe_id, category=cat)
                category_aggregations.append(cat_agg)

        glob_agg = aggregate_metrics(pipe_cases, pipeline_id=pipe_id, category=None)
        global_aggregations.append(glob_agg)

    # Determinismo por defecto derivado del manifest
    ts = fixed_timestamp or manifest.created_at

    return BenchmarkReport(
        schema_version=1,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        generated_at=ts,
        pipelines=active_pipelines,
        case_results=case_results,
        category_aggregations=category_aggregations,
        global_aggregations=global_aggregations,
        summary={
            "total_catalogued_cases": len(sorted_cases),
            "total_evaluated_cases": len(evaluable_cases),
            "total_excluded_cases": len(excluded_cases),
            "excluded_cases": excluded_summary,
            "categories": categories,
            "pipeline_ids": [p.pipeline_id for p in active_pipelines],
        },
    )


def _fmt_pct(m: MetricValue) -> str:
    """Formatea una métrica como porcentaje con conteos exactos."""
    pct = m.value * 100.0
    return f"{pct:.1f}% ({m.numerator}/{m.denominator})"


def render_report_markdown(report: BenchmarkReport) -> str:
    """Renderiza el informe de benchmark en formato Markdown determinista."""
    lines: List[str] = []

    total_cat = report.summary.get("total_catalogued_cases", len(report.case_results))
    total_eval = report.summary.get("total_evaluated_cases", len({c.case_id for c in report.case_results}))
    excluded = report.summary.get("excluded_cases", [])

    lines.append("# Informe de Benchmark: Extracción de Estadillos")
    lines.append("")
    lines.append(f"- **Dataset ID:** `{report.dataset_id}` (v`{report.dataset_version}`)")
    lines.append(f"- **Generado el:** `{report.generated_at}`")
    lines.append(f"- **Casos catalogados:** `{total_cat}`")
    lines.append(f"- **Casos evaluados cuantitativamente:** `{total_eval}`")
    if excluded:
        excl_str = ", ".join(f"`{e['case_id']}` ({e['category']})" for e in excluded)
        lines.append(f"- **Casos excluidos de evaluación tabular:** `{len(excluded)}` ({excl_str})")
    lines.append(f"- **Pipelines comparados:** {', '.join(f'`{p.pipeline_id}`' for p in report.pipelines)}")
    lines.append("")

    # 1. Comparación Global Micro-agregada
    lines.append("## 1. Comparación Global (Micro-agregación)")
    lines.append("")
    lines.append("La micro-agregación suma los numeradores y denominadores exactos de todos los casos evaluados:")
    lines.append("")

    header_cols = ["Métrica"] + [f"`{p.pipeline_id}`" for p in report.pipelines]
    lines.append("| " + " | ".join(header_cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(header_cols)) + " |")

    # Mapa global por pipeline
    glob_map = {g.pipeline_id: g for g in report.global_aggregations}

    metrics_keys = [
        ("row_precision_micro", "row_precision"),
        ("row_recall_micro", "row_recall"),
        ("col_exact_micro", "col_exact"),
        ("fil_exact_micro", "fil_exact"),
        ("species_exact_micro", "species_exact"),
        ("height_exact_micro", "height_exact"),
        ("photo_exact_micro", "photo_exact"),
        ("bbch_exact_micro", "**bbch_exact**"),
    ]

    for field_name, label in metrics_keys:
        row_vals = [label]
        for p in report.pipelines:
            g = glob_map.get(p.pipeline_id)
            if g:
                m: MetricValue = getattr(g, field_name)
                row_vals.append(_fmt_pct(m))
            else:
                row_vals.append("N/A")
        lines.append("| " + " | ".join(row_vals) + " |")
    lines.append("")

    # 2. Sección Destacada BBCH
    lines.append("## 2. Sección Destacada: Exactitud Fenológica BBCH (`bbch_exact`)")
    lines.append("")
    lines.append("La métrica `bbch_exact` evalúa la transcripción exacta y canónica del estado BBCH en filas emparejadas:")
    lines.append("")

    bbch_headers = ["Categoría"] + [f"`{p.pipeline_id}`" for p in report.pipelines]
    lines.append("| " + " | ".join(bbch_headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(bbch_headers)) + " |")

    categories = sorted(list({c.category for c in report.case_results}))
    for cat in categories:
        cat_vals = [cat]
        for p in report.pipelines:
            c_agg = next((ca for ca in report.category_aggregations if ca.pipeline_id == p.pipeline_id and ca.category == cat), None)
            if c_agg:
                cat_vals.append(_fmt_pct(c_agg.bbch_exact_micro))
            else:
                cat_vals.append("N/A")
        lines.append("| " + " | ".join(cat_vals) + " |")

    # Fila total global para BBCH
    tot_bbch_vals = ["**TOTAL GLOBAL**"]
    for p in report.pipelines:
        g = glob_map.get(p.pipeline_id)
        if g:
            tot_bbch_vals.append(_fmt_pct(g.bbch_exact_micro))
        else:
            tot_bbch_vals.append("N/A")
    lines.append("| " + " | ".join(tot_bbch_vals) + " |")
    lines.append("")

    # 3. Desglose por Categoría
    lines.append("## 3. Desglose Detallado por Categoría")
    lines.append("")
    for cat in categories:
        lines.append(f"### Categoría: `{cat}`")
        lines.append("")
        cat_headers = ["Métrica"] + [f"`{p.pipeline_id}`" for p in report.pipelines]
        lines.append("| " + " | ".join(cat_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(cat_headers)) + " |")

        for field_name, label in metrics_keys:
            row_vals = [label]
            for p in report.pipelines:
                c_agg = next((ca for ca in report.category_aggregations if ca.pipeline_id == p.pipeline_id and ca.category == cat), None)
                if c_agg:
                    m = getattr(c_agg, field_name)
                    row_vals.append(_fmt_pct(m))
                else:
                    row_vals.append("N/A")
            lines.append("| " + " | ".join(row_vals) + " |")
        lines.append("")

    # 4. Detalle por Caso Individual
    lines.append("## 4. Detalle por Caso Individual")
    lines.append("")
    cases = sorted(list({c.case_id for c in report.case_results}))
    for cid in cases:
        lines.append(f"### Caso: `{cid}`")
        lines.append("")
        case_headers = ["Pipeline", "Filas (Ref/Pred/Match)", "row_prec", "row_rec", "col", "fil", "species", "height", "photo", "bbch"]
        lines.append("| " + " | ".join(case_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(case_headers)) + " |")

        for p in report.pipelines:
            cm = next((c for c in report.case_results if c.case_id == cid and c.pipeline_id == p.pipeline_id), None)
            if cm:
                rows_str = f"{cm.total_reference_rows}/{cm.total_predicted_rows}/{cm.matched_rows}"
                lines.append(
                    f"| `{p.pipeline_id}` | {rows_str} | "
                    f"{_fmt_pct(cm.row_precision)} | {_fmt_pct(cm.row_recall)} | "
                    f"{_fmt_pct(cm.col_exact)} | {_fmt_pct(cm.fil_exact)} | "
                    f"{_fmt_pct(cm.species_exact)} | {_fmt_pct(cm.height_exact)} | "
                    f"{_fmt_pct(cm.photo_exact)} | {_fmt_pct(cm.bbch_exact)} |"
                )
        lines.append("")

    # 5. Casos Catalogados Excluidos de Evaluación Tabular
    if excluded:
        lines.append("## 5. Casos Catalogados Excluidos de Evaluación Tabular")
        lines.append("")
        lines.append("Los siguientes casos se encuentran catalogados en el dataset pero no se evalúan estructuralmente en PR 8 (croquis/esquemas sin DiagramIR):")
        lines.append("")
        for e in excluded:
            desc = f": {e['description']}" if e.get("description") else ""
            lines.append(f"- **`{e['case_id']}`** (categoría: `{e['category']}`){desc}")
        lines.append("")

    # 6. Notas y Metodología
    lines.append("## 6. Notas Metodológicas")
    lines.append("")
    lines.append("1. **Asignación global determinista (Húngaro):** El emparejamiento uno-a-uno utiliza el algoritmo Kuhn-Munkres para resolver la asignación óptima máxima global en $O(N^3)$ con desempates jerárquicos deterministas.")
    lines.append("2. **No circularidad estricta (Leave-One-Field-Out):** Cada métrica de campo (`col_exact`, `fil_exact`, `species_exact`, `height_exact`, `photo_exact`, `bbch_exact`) excluye su propio campo del cálculo de similitud y asignación, impidiendo el autoemparejamiento circular. Para row matching se exige identidad explícita por ID o al menos dos campos no vacíos concordantes.")
    lines.append("3. **Exclusión de croquis (PR 8):** Las páginas catalogadas como croquis/sketch no se evalúan cuantitativamente a nivel tabular hasta la entrega de DiagramIR en PR 9/10, por lo que no afectan a los denominadores ni agregados globales.")
    lines.append("4. **Exactitud estricta:** No se aplican tolerancias numéricas ocultas en `height_exact` ni correcciones heurísticas en `bbch_exact`.")
    lines.append("5. **Datos sintéticos de prueba:** Los fixtures incluidos en el repositorio son sintéticos y sirven para verificar la infraestructura offline; no constituyen prueba científica de superioridad de un modelo.")
    lines.append("")

    return "\n".join(lines)


def save_benchmark_report(report: BenchmarkReport, output_dir: Path) -> Tuple[Path, Path]:
    """Guarda atómicamente el informe de benchmark en report.json y report.md."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "report.json"
    md_path = output_dir / "report.md"

    json_content = report.model_dump_json(indent=2)
    md_content = render_report_markdown(report)

    write_atomic_file(json_path, json_content)
    write_atomic_file(md_path, md_content)

    return json_path, md_path
