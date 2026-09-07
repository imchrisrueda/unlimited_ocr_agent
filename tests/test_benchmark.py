import io
import json
import math
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import (
    EstadilloDocument,
    EstadilloPage,
    EstadilloRow,
    EstadilloDocHeader,
    EstadilloPageHeader,
)
from src.fieldnotes.benchmark.schemas import (
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
from src.fieldnotes.benchmark.matching import (
    match_rows_deterministic,
    compute_candidate_similarity,
    hungarian_max_weight,
    RowMatch,
    MatchResult,
    get_canonical_col,
    get_canonical_fil,
    get_canonical_species,
    get_canonical_height,
    get_canonical_photo,
    get_canonical_bbch,
    get_canonical_id,
)
from src.fieldnotes.benchmark.metrics import compute_case_metrics
from src.fieldnotes.benchmark.report import (
    aggregate_metrics,
    generate_benchmark_report,
    render_report_markdown,
    save_benchmark_report,
)
from src.fieldnotes.benchmark.cli import build_parser, main as cli_main


def _ev_str(val, page=1, unc=False):
    return EvidenceValue[str](raw=val, normalized=val, source_page=page, uncertain=unc) if val is not None else None

def _ev_int(val, page=1, unc=False):
    return EvidenceValue[int](raw=str(val), normalized=val, source_page=page, uncertain=unc) if val is not None else None

def _ev_float(val, page=1, unc=False):
    return EvidenceValue[float](raw=str(val), normalized=float(val), source_page=page, uncertain=unc) if val is not None else None


class TestBenchmarkSchemasAndInvariants(unittest.TestCase):
    """Pruebas de esquemas Pydantic v2, invariantes numéricas y validación de confinamiento."""

    def test_metric_value_valid_and_bounds(self):
        m = MetricValue.from_counts(4, 5)
        self.assertEqual(m.numerator, 4)
        self.assertEqual(m.denominator, 5)
        self.assertAlmostEqual(m.value, 0.8)

        # Denominador cero
        m0 = MetricValue.from_counts(0, 0, zero_denominator_value=1.0)
        self.assertEqual(m0.value, 1.0)
        self.assertEqual(m0.numerator, 0)
        self.assertEqual(m0.denominator, 0)

        # Denominador cero con default 0.0
        m0_zero = MetricValue.from_counts(0, 0, zero_denominator_value=0.0)
        self.assertEqual(m0_zero.value, 0.0)

    def test_metric_value_rejects_numerator_greater_than_denominator(self):
        # finding 5: nunca clamp si numerator > denominator
        with self.assertRaises(ValueError):
            MetricValue.from_counts(5, 4)

        with self.assertRaises(ValidationError):
            MetricValue(numerator=5, denominator=4, value=1.0)

    def test_metric_value_rejects_negative_counts(self):
        with self.assertRaises(ValueError):
            MetricValue.from_counts(-1, 5)
        with self.assertRaises(ValueError):
            MetricValue.from_counts(1, -5)

    def test_metric_value_rejects_nonzero_numerator_with_zero_denominator(self):
        with self.assertRaises(ValueError):
            MetricValue.from_counts(1, 0)
        with self.assertRaises(ValidationError):
            MetricValue(numerator=1, denominator=0, value=0.0)

    def test_metric_value_rejects_nan_and_inf(self):
        with self.assertRaises(ValidationError):
            MetricValue(numerator=1, denominator=1, value=float("nan"))
        with self.assertRaises(ValidationError):
            MetricValue(numerator=1, denominator=1, value=float("inf"))
        with self.assertRaises(ValidationError):
            MetricValue(numerator=1, denominator=1, value=-0.1)
        with self.assertRaises(ValidationError):
            MetricValue(numerator=1, denominator=1, value=1.1)

    def test_metric_value_extra_forbid(self):
        with self.assertRaises(ValidationError):
            MetricValue.model_validate({"numerator": 1, "denominator": 2, "value": 0.5, "extra_field": "bad"})

    def test_manifest_duplicate_case_id_rejected(self):
        with self.assertRaises(ValidationError):
            BenchmarkManifest(
                dataset_id="test",
                dataset_version="1.0.0",
                created_at="2026-08-27T00:00:00Z",
                pipelines=[PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1", version="1.0")],
                cases=[
                    BenchmarkCase(case_id="c1", category="easy", ground_truth_path="gt/c1.json", predictions={"p1": "pred/c1.json"}),
                    BenchmarkCase(case_id="c1", category="hard", ground_truth_path="gt/c2.json", predictions={"p1": "pred/c2.json"}),
                ],
            )

    def test_manifest_duplicate_pipeline_id_rejected(self):
        with self.assertRaises(ValidationError):
            BenchmarkManifest(
                dataset_id="test",
                dataset_version="1.0.0",
                created_at="2026-08-27T00:00:00Z",
                pipelines=[
                    PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1", version="1.0"),
                    PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1 dup", version="1.0"),
                ],
            )

    def test_manifest_unknown_pipeline_in_case_predictions_rejected(self):
        with self.assertRaises(ValidationError):
            BenchmarkManifest(
                dataset_id="test",
                dataset_version="1.0.0",
                created_at="2026-08-27T00:00:00Z",
                pipelines=[PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1", version="1.0")],
                cases=[
                    BenchmarkCase(
                        case_id="c1",
                        category="easy",
                        ground_truth_path="gt/c1.json",
                        predictions={"unknown_pipeline": "pred/c1.json"},
                    )
                ],
            )

    def test_validate_confined_relative_path_success(self):
        base = Path("tests/fixtures/benchmark").resolve()
        res = validate_confined_relative_path("ground_truth/case_01_easy_gt.json", base)
        self.assertTrue(res.is_relative_to(base))

    def test_validate_confined_relative_path_rejects_escapes(self):
        base = Path("tests/fixtures/benchmark").resolve()
        with self.assertRaises(ValueError):
            validate_confined_relative_path("../outside.json", base)
        with self.assertRaises(ValueError):
            validate_confined_relative_path("ground_truth/../../outside.json", base)
        with self.assertRaises(ValueError):
            validate_confined_relative_path("/etc/passwd", base)
        with self.assertRaises(ValueError):
            validate_confined_relative_path("C:\\Windows\\System32", base)
        with self.assertRaises(ValueError):
            validate_confined_relative_path("ground_truth\\with_backslash.json", base)


class TestRowMatchingAndAlgorithms(unittest.TestCase):
    """Pruebas exhaustivas para asignación global óptima (Húngaro), LOFO y no-circularidad."""

    def test_finding1_circular_matching_regression_demo(self):
        """Regresión exacta hallazgo 1: coincidencia única aislada de BBCH no debe emparejar filas distintas."""
        ref = EstadilloRow(
            source_page=1,
            col=_ev_int(1),
            fil=_ev_int(1),
            especie=_ev_str("P"),
            foto=_ev_str("a"),
            bbch=_ev_str("22"),
        )
        pred = EstadilloRow(
            source_page=1,
            col=_ev_int(9),
            fil=_ev_int(9),
            especie=_ev_str("M"),
            foto=_ev_str("z"),
            bbch=_ev_str("22"),
        )

        # 1. Matching base: sólo 1 campo concordante (bbch=22), exige al menos 2 -> matched = 0
        res = match_rows_deterministic([ref], [pred], excluded_field=None)
        self.assertEqual(len(res.matched_pairs), 0, "No debe emparejar filas con sólo 1 campo coincidente aislado")
        self.assertEqual(len(res.unmatched_reference), 1)
        self.assertEqual(len(res.unmatched_predicted), 1)

        # 2. Métricas de caso
        doc_ref = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[ref])])
        doc_pred = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred])])

        cm = compute_case_metrics("c_reg", "easy", "p", doc_ref, doc_pred)
        self.assertEqual(cm.matched_rows, 0)
        self.assertEqual(cm.row_precision.value, 0.0)
        self.assertEqual(cm.row_recall.value, 0.0)
        self.assertEqual(cm.bbch_exact.value, 0.0, "bbch_exact NO puede ser 1.0 por autoemparejamiento espurio")
        self.assertEqual(cm.bbch_exact.numerator, 0)
        self.assertEqual(cm.bbch_exact.denominator, 0)

    def test_hungarian_solves_greedy_failure_case(self):
        """Prueba donde el algoritmo greedy local falla y el algoritmo Húngaro encuentra la asignación global óptima."""
        # Configurar matriz de similitudes:
        # Ref 0 tiene mayor similitud local con Pred 0 (85) que con Pred 1 (80)
        # Ref 1 SOLO es compatible con Pred 0 (80), y con Pred 1 tiene 10 (o 0)
        # Greedy elegiría (0, 0) [85] dejando (1, 1) [10] -> Total = 95
        # Asignación global Húngara elige (0, 1) [80] y (1, 0) [80] -> Total = 160 (empareja 2 filas válidas)
        cost_matrix = [
            [85, 80],
            [80, 10],
        ]
        matches = hungarian_max_weight(cost_matrix)
        self.assertEqual(matches, [(0, 1), (1, 0)], "Húngaro debe encontrar la asignación global máxima")
        total_weight = sum(cost_matrix[r][c] for r, c in matches)
        self.assertEqual(total_weight, 160)

    def test_leave_one_field_out_matching_and_scoring(self):
        """Verifica que el matching LOFO para cada campo excluye el campo objetivo sin circularidad."""
        # Fila con col=2 predicho en vez de col=1, pero fil=26, especie=P, altura=10.0, foto=43, bbch=22 coinciden
        ref = EstadilloRow(
            source_page=1,
            col=_ev_int(1),
            fil=_ev_int(26),
            especie=_ev_str("P"),
            altura_cm=_ev_float(10.0),
            foto=_ev_str("43"),
            bbch=_ev_str("22"),
        )
        pred = EstadilloRow(
            source_page=1,
            col=_ev_int(2),
            fil=_ev_int(26),
            especie=_ev_str("P"),
            altura_cm=_ev_float(10.0),
            foto=_ev_str("43"),
            bbch=_ev_str("22"),
        )

        doc_ref = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[ref])])
        doc_pred = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred])])

        cm = compute_case_metrics("c_lofo", "easy", "p", doc_ref, doc_pred)
        self.assertEqual(cm.matched_rows, 1)
        self.assertEqual(cm.row_precision.value, 1.0)
        self.assertEqual(cm.row_recall.value, 1.0)
        # col_exact evaluado independientemente debe ser 0.0 (0/1)
        self.assertEqual(cm.col_exact.value, 0.0)
        self.assertEqual(cm.col_exact.numerator, 0)
        self.assertEqual(cm.col_exact.denominator, 1)
        # bbch_exact evaluado independientemente debe ser 1.0 (1/1)
        self.assertEqual(cm.bbch_exact.value, 1.0)
        self.assertEqual(cm.bbch_exact.numerator, 1)
        self.assertEqual(cm.bbch_exact.denominator, 1)

    def test_explicit_id_matching(self):
        """Verifica que si existe ID explícito idéntico, se empareja directamente; si hay conflicto, se rechaza."""
        r1 = EstadilloRow(source_page=1, id=_ev_str("REC-001"), col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"))
        p1 = EstadilloRow(source_page=1, id=_ev_str("REC-001"), col=_ev_int(1), fil=_ev_int(2), especie=_ev_str("P"))
        p2_conflict = EstadilloRow(source_page=1, id=_ev_str("REC-002"), col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"))

        # Mismo ID -> match
        res_match = match_rows_deterministic([r1], [p1])
        self.assertEqual(len(res_match.matched_pairs), 1)

        # ID en conflicto explícito -> rechazo si no hay otras opciones
        score, fields, num_m = compute_candidate_similarity(r1, p2_conflict)
        self.assertEqual(score, 0.0)

    def test_perfect_matching(self):
        rows = [
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(26), especie=_ev_str("P"), altura_cm=_ev_float(10.0), bbch=_ev_str("22")),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(25), especie=_ev_str("H"), altura_cm=_ev_float(12.0), bbch=_ev_str("22")),
        ]
        res = match_rows_deterministic(rows, rows)
        self.assertEqual(len(res.matched_pairs), 2)
        self.assertEqual(len(res.unmatched_reference), 0)
        self.assertEqual(len(res.unmatched_predicted), 0)

    def test_reordered_rows_matching(self):
        r1 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(26), especie=_ev_str("P"), altura_cm=_ev_float(10.0), bbch=_ev_str("22"))
        r2 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(25), especie=_ev_str("H"), altura_cm=_ev_float(12.0), bbch=_ev_str("22"))
        r3 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(24), especie=_ev_str("R"), altura_cm=_ev_float(14.0), bbch=_ev_str("23"))

        ref_rows = [r1, r2, r3]
        pred_rows = [r3, r1, r2]

        res = match_rows_deterministic(ref_rows, pred_rows)
        self.assertEqual(len(res.matched_pairs), 3)

        m_r1 = next(m for m in res.matched_pairs if m.ref_index == 0)
        self.assertEqual(m_r1.pred_index, 1)

        m_r2 = next(m for m in res.matched_pairs if m.ref_index == 1)
        self.assertEqual(m_r2.pred_index, 2)

        m_r3 = next(m for m in res.matched_pairs if m.ref_index == 2)
        self.assertEqual(m_r3.pred_index, 0)

    def test_missing_rows_matching(self):
        r1 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(26), especie=_ev_str("P"), altura_cm=_ev_float(10.0), bbch=_ev_str("22"))
        r2 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(25), especie=_ev_str("H"), altura_cm=_ev_float(12.0), bbch=_ev_str("22"))
        r3 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(24), especie=_ev_str("R"), altura_cm=_ev_float(14.0), bbch=_ev_str("23"))

        ref_rows = [r1, r2, r3]
        pred_rows = [r1]

        res = match_rows_deterministic(ref_rows, pred_rows)
        self.assertEqual(len(res.matched_pairs), 1)
        self.assertEqual(len(res.unmatched_reference), 2)
        self.assertEqual(len(res.unmatched_predicted), 0)

    def test_extra_and_duplicate_predicted_rows(self):
        r1 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(26), especie=_ev_str("P"), altura_cm=_ev_float(10.0), bbch=_ev_str("22"))
        ref_rows = [r1]

        r_spurious = EstadilloRow(source_page=1, col=_ev_int(99), fil=_ev_int(99), especie=_ev_str("X"), altura_cm=_ev_float(999.0), bbch=_ev_str("99"))
        pred_rows = [r1, r1, r_spurious]

        res = match_rows_deterministic(ref_rows, pred_rows)
        self.assertEqual(len(res.matched_pairs), 1)
        self.assertEqual(len(res.unmatched_reference), 0)
        self.assertEqual(len(res.unmatched_predicted), 2)


class TestBenchmarkMetrics(unittest.TestCase):
    """Pruebas para el cálculo exacto de las 8 métricas obligatorias."""

    def test_perfect_case_metrics(self):
        row = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(26), especie=_ev_str("P"), altura_cm=_ev_float(10.0), foto=_ev_str("43"), bbch=_ev_str("22"))
        doc = EstadilloDocument(source_file="doc.pdf", pages=[EstadilloPage(page_number=1, rows=[row])])

        cm = compute_case_metrics("c1", "easy", "pipe", doc, doc)
        self.assertEqual(cm.total_reference_rows, 1)
        self.assertEqual(cm.total_predicted_rows, 1)
        self.assertEqual(cm.matched_rows, 1)
        self.assertEqual(cm.row_precision.value, 1.0)
        self.assertEqual(cm.row_recall.value, 1.0)
        self.assertEqual(cm.col_exact.value, 1.0)
        self.assertEqual(cm.fil_exact.value, 1.0)
        self.assertEqual(cm.species_exact.value, 1.0)
        self.assertEqual(cm.height_exact.value, 1.0)
        self.assertEqual(cm.photo_exact.value, 1.0)
        self.assertEqual(cm.bbch_exact.value, 1.0)

    def test_empty_reference_and_empty_prediction(self):
        doc = EstadilloDocument(source_file="empty.pdf", pages=[EstadilloPage(page_number=1, rows=[])])
        cm = compute_case_metrics("c_empty", "easy", "pipe", doc, doc)
        self.assertEqual(cm.total_reference_rows, 0)
        self.assertEqual(cm.total_predicted_rows, 0)
        self.assertEqual(cm.matched_rows, 0)
        self.assertEqual(cm.row_precision.value, 1.0)
        self.assertEqual(cm.row_recall.value, 1.0)
        self.assertEqual(cm.bbch_exact.value, 1.0)
        self.assertEqual(cm.species_exact.value, 1.0)

    def test_missing_values_policy(self):
        ref_rows = [
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), foto=None),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(2), especie=_ev_str("P"), foto=_ev_str("10")),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(3), especie=_ev_str("P"), foto=None),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(4), especie=_ev_str("P"), foto=_ev_str("20")),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(5), especie=_ev_str("P"), foto=_ev_str("30")),
        ]
        pred_rows = [
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), foto=None),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(2), especie=_ev_str("P"), foto=None),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(3), especie=_ev_str("P"), foto=_ev_str("99")),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(4), especie=_ev_str("P"), foto=_ev_str("20")),
            EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(5), especie=_ev_str("P"), foto=_ev_str("31")),
        ]
        ref_doc = EstadilloDocument(source_file="ref.pdf", pages=[EstadilloPage(page_number=1, rows=ref_rows)])
        pred_doc = EstadilloDocument(source_file="pred.pdf", pages=[EstadilloPage(page_number=1, rows=pred_rows)])

        cm = compute_case_metrics("c_mv", "easy", "pipe", ref_doc, pred_doc)
        self.assertEqual(cm.matched_rows, 5)
        self.assertEqual(cm.photo_exact.numerator, 2)
        self.assertEqual(cm.photo_exact.denominator, 5)
        self.assertAlmostEqual(cm.photo_exact.value, 0.4)

    def test_bbch_exact_strict_preservation(self):
        ref_row = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), bbch=_ev_str("22"))
        pred_row_exact = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), bbch=_ev_str("22"))
        pred_row_truncated = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), bbch=_ev_str("225"))
        pred_row_confused = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), bbch=_ev_str("28"))

        doc_ref = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[ref_row])])
        doc_exact = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_row_exact])])
        doc_trunc = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_row_truncated])])
        doc_conf = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_row_confused])])

        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_exact).bbch_exact.value, 1.0)
        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_trunc).bbch_exact.value, 0.0)
        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_conf).bbch_exact.value, 0.0)

    def test_height_exact_strict_comparison(self):
        ref_row = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), altura_cm=_ev_float(12.5))
        pred_exact = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), altura_cm=_ev_float(12.5))
        pred_close = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"), altura_cm=_ev_float(12.51))

        doc_ref = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[ref_row])])
        doc_exact = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_exact])])
        doc_close = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_close])])

        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_exact).height_exact.value, 1.0)
        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_close).height_exact.value, 0.0)

    def test_species_canonical_comparison(self):
        ref_row = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=_ev_str("P"))
        pred_ap = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=EvidenceValue[str](raw="Ap", normalized=None, source_page=1))
        pred_m2 = EstadilloRow(source_page=1, col=_ev_int(1), fil=_ev_int(1), especie=EvidenceValue[str](raw="M2", normalized=None, source_page=1))

        doc_ref = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[ref_row])])
        doc_ap = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_ap])])
        doc_m2 = EstadilloDocument(source_file="d.pdf", pages=[EstadilloPage(page_number=1, rows=[pred_m2])])

        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_ap).species_exact.value, 1.0)
        self.assertEqual(compute_case_metrics("c", "e", "p", doc_ref, doc_m2).species_exact.value, 0.0)


class TestValidationBeforeMetricsAndCrossChecks(unittest.TestCase):
    """Pruebas de validación estricta de IDs de GT/predicción y parámetros antes de calcular métricas."""

    def setUp(self):
        self.manifest_path = Path("tests/fixtures/benchmark/manifest.json").resolve()
        self.dataset_dir = self.manifest_path.parent
        self.manifest = BenchmarkManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))

    def test_gt_case_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copytree(self.dataset_dir, tmp / "dataset")
            # Corromper case_id en un GT
            gt_file = tmp / "dataset" / "ground_truth" / "case_01_easy_gt.json"
            gt_data = json.loads(gt_file.read_text(encoding="utf-8"))
            gt_data["case_id"] = "wrong_case_id"
            gt_file.write_text(json.dumps(gt_data), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                generate_benchmark_report(self.manifest, tmp / "dataset")
            self.assertIn("mismatch", str(ctx.exception).lower())

    def test_prediction_case_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copytree(self.dataset_dir, tmp / "dataset")
            pred_file = tmp / "dataset" / "predictions" / "case_01_ocr_only.json"
            pred_data = json.loads(pred_file.read_text(encoding="utf-8"))
            pred_data["case_id"] = "wrong_case_id"
            pred_file.write_text(json.dumps(pred_data), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                generate_benchmark_report(self.manifest, tmp / "dataset")
            self.assertIn("mismatch", str(ctx.exception).lower())

    def test_prediction_pipeline_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copytree(self.dataset_dir, tmp / "dataset")
            pred_file = tmp / "dataset" / "predictions" / "case_01_ocr_only.json"
            pred_data = json.loads(pred_file.read_text(encoding="utf-8"))
            pred_data["pipeline_id"] = "wrong_pipeline_id"
            pred_file.write_text(json.dumps(pred_data), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                generate_benchmark_report(self.manifest, tmp / "dataset")
            self.assertIn("mismatch", str(ctx.exception).lower())

    def test_empty_requested_pipelines_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            generate_benchmark_report(self.manifest, self.dataset_dir, pipelines=[])
        self.assertIn("selección vacía no permitida", str(ctx.exception))

    def test_nonexistent_requested_pipeline_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            generate_benchmark_report(self.manifest, self.dataset_dir, pipelines=["non_existent_pipe"])
        self.assertIn("no existe en el manifiesto", str(ctx.exception))


    def test_evaluable_case_without_ground_truth_path_rejected(self):
        with self.assertRaises(ValidationError):
            BenchmarkManifest(
                dataset_id="test",
                dataset_version="1.0.0",
                created_at="2026-08-27T00:00:00Z",
                pipelines=[PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1", version="1.0")],
                cases=[
                    BenchmarkCase(case_id="c1", category="easy", evaluate_structured=True, ground_truth_path=None),
                ],
            )

    def test_excluded_case_without_ground_truth_or_predictions_allowed(self):
        manifest = BenchmarkManifest(
            dataset_id="test",
            dataset_version="1.0.0",
            created_at="2026-08-27T00:00:00Z",
            pipelines=[PipelineConfig(pipeline_id="p1", pipeline_name="Pipe 1", version="1.0")],
            cases=[
                BenchmarkCase(
                    case_id="c_sketch",
                    category="sketch",
                    evaluate_structured=False,
                    ground_truth_path=None,
                    predictions={},
                ),
            ],
        )
        self.assertEqual(len(manifest.cases), 1)
        self.assertFalse(manifest.cases[0].evaluate_structured)


class TestAggregationsReportsAndDeterminism(unittest.TestCase):
    """Pruebas para agregación, generación de reportes y determinismo byte-a-byte por defecto."""

    def setUp(self):
        self.manifest_path = Path("tests/fixtures/benchmark/manifest.json").resolve()
        self.dataset_dir = self.manifest_path.parent

    def test_generate_benchmark_report_on_fixtures(self):
        manifest_data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest = BenchmarkManifest.model_validate(manifest_data)

        report = generate_benchmark_report(manifest=manifest, dataset_dir=self.dataset_dir)

        self.assertEqual(report.dataset_id, "fieldnotes-benchmark-fixtures-v1")
        self.assertEqual(report.generated_at, "2026-08-27T00:00:00Z")
        self.assertEqual(len(report.pipelines), 2)
        # 4 evaluables * 2 pipelines = 8 (case_05_sketch está excluido de evaluación tabular)
        self.assertEqual(len(report.case_results), 8)
        self.assertEqual(len(report.category_aggregations), 8)  # 4 categorias evaluadas * 2 pipelines
        self.assertEqual(len(report.global_aggregations), 2)

        self.assertEqual(report.summary["total_catalogued_cases"], 5)
        self.assertEqual(report.summary["total_evaluated_cases"], 4)
        self.assertEqual(report.summary["total_excluded_cases"], 1)
        self.assertEqual(len(report.summary["excluded_cases"]), 1)
        self.assertEqual(report.summary["excluded_cases"][0]["case_id"], "case_05_sketch")

        # Verificar que la categoría sketch NO aparece en las tablas de métricas
        categories_in_agg = {ca.category for ca in report.category_aggregations}
        self.assertNotIn("sketch", categories_in_agg)

        md_text = render_report_markdown(report)
        self.assertIn("Informe de Benchmark: Extracción de Estadillos", md_text)
        self.assertIn("bbch_exact", md_text)
        self.assertIn("Casos catalogados:** `5`", md_text)
        self.assertIn("Casos evaluados cuantitativamente:** `4`", md_text)
        self.assertIn("Casos excluidos de evaluación tabular:** `1`", md_text)
        self.assertIn("Casos Catalogados Excluidos de Evaluación Tabular", md_text)

    def test_adversarial_sketch_prediction_does_not_affect_aggregates(self):
        """Prueba adversarial: un caso sketch excluido nunca afecta agregados ni denominadores."""
        manifest_data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest = BenchmarkManifest.model_validate(manifest_data)

        clean_report = generate_benchmark_report(manifest, self.dataset_dir)

        # Modificar el caso sketch en el manifiesto para asociarle predicciones arbitrarias
        manifest_data_adv = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        for c in manifest_data_adv["cases"]:
            if c["case_id"] == "case_05_sketch":
                c["predictions"] = {"ocr_only": "predictions/case_05_ocr_only.json"}
        manifest_adv = BenchmarkManifest.model_validate(manifest_data_adv)

        adv_report = generate_benchmark_report(manifest_adv, self.dataset_dir)

        # Verificar que todas las agregaciones globales y por categoría son idénticas
        self.assertEqual(clean_report.global_aggregations, adv_report.global_aggregations)
        self.assertEqual(clean_report.category_aggregations, adv_report.category_aggregations)
        self.assertEqual(clean_report.case_results, adv_report.case_results)

    def test_deterministic_default_byte_to_byte_reproducibility(self):
        """finding 3: dos ejecuciones sin --timestamp generan bytes idénticos derivados del manifest."""
        manifest_data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest = BenchmarkManifest.model_validate(manifest_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            report1 = generate_benchmark_report(manifest, self.dataset_dir)  # Sin fixed_timestamp
            json1, md1 = save_benchmark_report(report1, out_dir / "run1")

            report2 = generate_benchmark_report(manifest, self.dataset_dir)  # Sin fixed_timestamp
            json2, md2 = save_benchmark_report(report2, out_dir / "run2")

            content_json1 = json1.read_bytes()
            content_json2 = json2.read_bytes()
            self.assertEqual(content_json1, content_json2, "report.json debe ser byte-a-byte idéntico por defecto")

            content_md1 = md1.read_bytes()
            content_md2 = md2.read_bytes()
            self.assertEqual(content_md1, content_md2, "report.md debe ser byte-a-byte idéntico por defecto")


class TestBenchmarkCLI(unittest.TestCase):
    """Pruebas para la interfaz de línea de comandos del benchmark."""

    def test_cli_execution_offline_and_unique_cases_count(self):
        """CLI imprime Casos catalogados, evaluados y excluidos de forma clara."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "bench_out"
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = cli_main([
                    "--manifest", "tests/fixtures/benchmark/manifest.json",
                    "--output-dir", str(out_dir),
                ])
            self.assertEqual(ret, 0)
            stdout_text = buf.getvalue()
            self.assertIn("Casos catalogados: 5", stdout_text)
            self.assertIn("Casos evaluados: 4", stdout_text)
            self.assertIn("Casos excluidos: 1", stdout_text)
            self.assertTrue((out_dir / "report.json").is_file())
            self.assertTrue((out_dir / "report.md").is_file())

    def test_cli_missing_manifest_returns_error(self):
        ret = cli_main(["--manifest", "non_existent_manifest.json"])
        self.assertEqual(ret, 1)

    def test_cli_run_pipelines_flag_informative_exit(self):
        ret = cli_main(["--run-pipelines"])
        self.assertEqual(ret, 1)


class TestOfflineIsolation(unittest.TestCase):
    """Prueba que garantiza que el benchmark offline no invoca red, GPU ni LM Studio."""

    def test_no_network_or_gpu_calls_during_benchmark(self):
        with patch.object(socket, "socket", side_effect=RuntimeError("Llamada de red prohibida en benchmark offline")):
            manifest_path = Path("tests/fixtures/benchmark/manifest.json").resolve()
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = BenchmarkManifest.model_validate(manifest_data)

            report = generate_benchmark_report(
                manifest=manifest,
                dataset_dir=manifest_path.parent,
            )
            self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()


