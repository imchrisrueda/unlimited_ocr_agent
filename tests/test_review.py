import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image
from pydantic import ValidationError

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.schemas.estadillo import (
    EstadilloHeader,
    EstadilloPageHeader,
    EstadilloDocHeader,
    EstadilloRow,
    EstadilloPage,
    EstadilloDocument,
)
from src.fieldnotes.schemas.dto import (
    CandidateVisualBBox1000,
    VisualBBox1000,
    validate_visual_bbox1000,
    VisualRegion1000DTO,
    visual_bbox1000_to_normalized,
    EstadilloHeaderDTO,
    EstadilloRowDTO,
    EstadilloPageDTO,
    dto_to_estadillo_page,
)
from src.fieldnotes.schemas.review import (
    ReviewIssue,
    NormalizedBBox,
    ReviewIssuesList,
    generate_issue_id,
    review_issues_to_json,
    review_issues_from_json,
)
from src.fieldnotes.review.issues import (
    derive_row_key,
    deduplicate_and_sort_issues,
    extract_review_issues_from_document,
    extract_invalid_region_issues,
    collect_dto_regions,
    match_region_for_issue,
)
from src.fieldnotes.review.crops import (
    validate_safe_crop_path,
    generate_crop_filename,
    crop_image_region,
    generate_review_crops_for_issues,
)
from src.fieldnotes.artifacts import PageArtifact
from src.fieldnotes.profiles.estadillo import EstadilloProfile


class TestReviewSchema(unittest.TestCase):
    def test_review_issue_strict_config_extra_forbid(self):
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="test_id_001",
                page=1,
                field="especie",
                reason="Duda",
                extra_forbidden_field="invalid",
            )

    def test_review_issue_valid_instantiation_and_roundtrip(self):
        issue = ReviewIssue(
            issue_id="p001_col1_fil26_especie_UNCERTAIN_EVIDENCE_12345678",
            page=1,
            field="especie",
            row_key="col1_fil26",
            reason="Valor incierto en fila [col1_fil26] campo 'especie' (raw: 'Mz')",
            candidates=["M", "Mz"],
            crop_path="review/p001_col1_fil26_especie.png",
            warning_code="UNCERTAIN_EVIDENCE",
            provenance={"source": "row_evidence", "raw": "Mz", "normalized": None},
        )
        json_str = issue.model_dump_json(indent=2)
        loaded = ReviewIssue.model_validate_json(json_str)
        self.assertEqual(loaded.issue_id, issue.issue_id)
        self.assertEqual(loaded.page, 1)
        self.assertEqual(loaded.field, "especie")
        self.assertEqual(loaded.row_key, "col1_fil26")
        self.assertEqual(loaded.candidates, ["M", "Mz"])
        self.assertEqual(loaded.crop_path, "review/p001_col1_fil26_especie.png")
        self.assertEqual(loaded.warning_code, "UNCERTAIN_EVIDENCE")

    def test_review_issue_page_none_for_document_level_issues(self):
        issue = ReviewIssue(
            issue_id="doc_header_conflict_12345678",
            page=None,
            field="document",
            reason="Conflicto global de cabecera",
            warning_code="HEADER_CONFLICT",
        )
        self.assertIsNone(issue.page)
        self.assertEqual(issue.field, "document")
        json_str = issue.model_dump_json(indent=2)
        loaded = ReviewIssue.model_validate_json(json_str)
        self.assertIsNone(loaded.page)

    def test_review_issue_crop_path_strict_review_prefix_validation(self):
        # 1. Ruta relativa POSIX válida bajo review/
        issue = ReviewIssue(
            issue_id="id1",
            page=1,
            field="altura_cm",
            reason="Duda",
            crop_path="review/p001_col1_fil26_altura_cm.png",
        )
        self.assertEqual(issue.crop_path, "review/p001_col1_fil26_altura_cm.png")

        # 2. None y vacío retornan None
        issue_none = ReviewIssue(issue_id="id2", page=1, field="altura_cm", reason="Duda", crop_path=None)
        self.assertIsNone(issue_none.crop_path)
        issue_empty = ReviewIssue(issue_id="id3", page=1, field="altura_cm", reason="Duda", crop_path="")
        self.assertIsNone(issue_empty.crop_path)

        # 3. Rechazar rutas relativas sin prefijo review/
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id4",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="foo.png",
            )
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id5",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="assets/crop.png",
            )

        # 4. Prohibir backslashes de Windows
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id6",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="review\\p001_altura.png",
            )

        # 5. Prohibir rutas absolutas o con letra de unidad
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id7",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="/review/p001_altura.png",
            )
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id8",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="C:/review/p001_altura.png",
            )

        # 6. Prohibir path traversal
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id9",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="review/../escaped.png",
            )

        # 7. Prohibir extensiones no PNG
        with self.assertRaises(ValidationError):
            ReviewIssue(
                issue_id="id10",
                page=1,
                field="altura_cm",
                reason="Duda",
                crop_path="review/crop.jpg",
            )

    def test_review_issues_list_json_serialization_empty_and_populated(self):
        empty_json = review_issues_to_json([])
        self.assertEqual(empty_json.strip(), "[]")
        loaded_empty = review_issues_from_json(empty_json)
        self.assertEqual(loaded_empty, [])

        issue1 = ReviewIssue(issue_id="id_1", page=1, field="f1", reason="r1")
        issue2 = ReviewIssue(issue_id="id_2", page=None, field="f2", reason="r2")
        populated_json = review_issues_to_json([issue1, issue2])
        loaded_pop = review_issues_from_json(populated_json)
        self.assertEqual(len(loaded_pop), 2)
        self.assertEqual(loaded_pop[0].issue_id, "id_1")
        self.assertIsNone(loaded_pop[1].page)

    def test_generate_issue_id_determinism_and_stability(self):
        id_1a = generate_issue_id(page=1, field="especie", row_key="col1_fil26", warning_code="UNRECOGNIZED_SPECIES", reason="Especie M2")
        id_1b = generate_issue_id(page=1, field="especie", row_key="col1_fil26", warning_code="UNRECOGNIZED_SPECIES", reason="Especie M2")
        self.assertEqual(id_1a, id_1b)

        id_doc_a = generate_issue_id(page=None, field="document", row_key=None, warning_code="HEADER_CONFLICT", reason="Conflicto")
        id_doc_b = generate_issue_id(page=None, field="document", row_key=None, warning_code="HEADER_CONFLICT", reason="Conflicto")
        self.assertEqual(id_doc_a, id_doc_b)
        self.assertTrue(id_doc_a.startswith("doc_"))


class TestNormalizedBBox(unittest.TestCase):
    def test_valid_bbox_coordinates(self):
        box = NormalizedBBox(x0=0.1, y0=0.2, x1=0.8, y1=0.9)
        self.assertEqual(box.x0, 0.1)
        self.assertEqual(box.y0, 0.2)
        self.assertEqual(box.x1, 0.8)
        self.assertEqual(box.y1, 0.9)

    def test_bbox_boundary_values(self):
        box = NormalizedBBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
        self.assertEqual(box.x0, 0.0)
        self.assertEqual(box.y0, 0.0)
        self.assertEqual(box.x1, 1.0)
        self.assertEqual(box.y1, 1.0)

    def test_bbox_out_of_bounds_rejected(self):
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=-0.1, y0=0.2, x1=0.8, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.2, x1=1.1, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=-0.01, x1=0.8, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.2, x1=0.8, y1=1.01)

    def test_bbox_inverted_coordinates_rejected(self):
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.8, y0=0.2, x1=0.1, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.9, x1=0.8, y1=0.2)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.5, y0=0.2, x1=0.5, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.5, x1=0.8, y1=0.5)

    def test_bbox_nan_and_inf_rejected(self):
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=float("nan"), y0=0.2, x1=0.8, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=float("inf"), x1=0.8, y1=0.9)
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.2, x1=float("-inf"), y1=0.9)

    def test_bbox_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            NormalizedBBox(x0=0.1, y0=0.2, x1=0.8, y1=0.9, extra="invalid")


class TestVisualBBox1000(unittest.TestCase):
    def test_candidate_visual_bbox1000_allows_deferred_validation_values(self):
        # Permite coordenadas invertidas sin fallar el parseo del DTO
        c_inv = CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105)
        self.assertEqual(c_inv.y0, 995)
        self.assertEqual(c_inv.y1, 105)

        # Permite coordenadas > 1000 o negativas
        c_large = CandidateVisualBBox1000(x0=0, y0=0, x1=1200, y1=500)
        self.assertEqual(c_large.x1, 1200)

        c_neg = CandidateVisualBBox1000(x0=-10, y0=0, x1=500, y1=500)
        self.assertEqual(c_neg.x0, -10)

    def test_candidate_visual_bbox1000_strict_types_rejection(self):
        # Rechaza tipos no enteros (no bool, float, str)
        with self.assertRaises(ValidationError):
            CandidateVisualBBox1000(x0=True, y0=0, x1=100, y1=100)
        with self.assertRaises(ValidationError):
            CandidateVisualBBox1000(x0=10.5, y0=0, x1=100, y1=100)
        with self.assertRaises(ValidationError):
            CandidateVisualBBox1000(x0="10", y0=0, x1=100, y1=100)
        with self.assertRaises(ValidationError):
            CandidateVisualBBox1000(x0=0, y0=0, x1=100, y1=100, extra="forbidden")

    def test_valid_visual_bbox1000(self):
        box = VisualBBox1000(x0=30, y0=220, x1=970, y1=270)
        self.assertEqual(box.x0, 30)
        self.assertEqual(box.y0, 220)
        self.assertEqual(box.x1, 970)
        self.assertEqual(box.y1, 270)

        # Roundtrip JSON
        json_str = box.model_dump_json()
        loaded = VisualBBox1000.model_validate_json(json_str)
        self.assertEqual(loaded.x0, 30)
        self.assertEqual(loaded.y0, 220)
        self.assertEqual(loaded.x1, 970)
        self.assertEqual(loaded.y1, 270)

    def test_boundary_values_at_limits(self):
        # Esquinas extremas 0 y 1000
        box_full = VisualBBox1000(x0=0, y0=0, x1=1000, y1=1000)
        self.assertEqual(box_full.x0, 0)
        self.assertEqual(box_full.y0, 0)
        self.assertEqual(box_full.x1, 1000)
        self.assertEqual(box_full.y1, 1000)

        # Caja mínima en origen
        box_min = VisualBBox1000(x0=0, y0=0, x1=1, y1=1)
        self.assertEqual(box_min.x1, 1)
        self.assertEqual(box_min.y1, 1)

        # Caja mínima en extremo superior
        box_max = VisualBBox1000(x0=999, y0=999, x1=1000, y1=1000)
        self.assertEqual(box_max.x0, 999)
        self.assertEqual(box_max.x1, 1000)

    def test_strict_types_rejection(self):
        # 1. Rechazar floats
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=0.5, y0=200, x1=900, y1=800)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=100, y0=200.0, x1=900, y1=800)

        # 2. Rechazar strings
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0="100", y0=200, x1=900, y1=800)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=100, y0="200", x1="900", y1=800)

        # 3. Rechazar booleans
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=True, y0=200, x1=900, y1=800)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=0, y0=False, x1=900, y1=800)

        # 4. Rechazar tipos estrictos desde JSON
        with self.assertRaises(ValidationError):
            VisualBBox1000.model_validate_json('{"x0": true, "y0": 200, "x1": 900, "y1": 800}')
        with self.assertRaises(ValidationError):
            VisualBBox1000.model_validate_json('{"x0": 10.5, "y0": 200, "x1": 900, "y1": 800}')
        with self.assertRaises(ValidationError):
            VisualBBox1000.model_validate_json('{"x0": "100", "y0": 200, "x1": 900, "y1": 800}')

    def test_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=10, y0=20, x1=30, y1=40, extra_field="forbidden")
        with self.assertRaises(ValidationError):
            VisualBBox1000.model_validate_json('{"x0": 10, "y0": 20, "x1": 30, "y1": 40, "extra": 1}')

    def test_degenerate_boxes_rejected(self):
        # x0 == x1
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=100, y0=100, x1=100, y1=200)
        # x0 > x1
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=200, y0=100, x1=100, y1=200)
        # y0 == y1
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=100, y0=100, x1=200, y1=100)
        # y0 > y1
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=100, y0=200, x1=200, y1=100)

    def test_out_of_range_coords_rejected(self):
        # Coordenadas negativas
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=-1, y0=0, x1=100, y1=100)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=0, y0=-10, x1=100, y1=100)

        # Coordenadas > 1000
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=0, y0=0, x1=1001, y1=100)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=0, y0=0, x1=100, y1=1001)
        with self.assertRaises(ValidationError):
            VisualBBox1000(x0=1001, y0=0, x1=1002, y1=100)

    def test_validate_visual_bbox1000_helper(self):
        # 1. Invertida devuelve None
        c_inv = CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105)
        self.assertIsNone(validate_visual_bbox1000(c_inv))

        # 2. Fuera de límites devuelve None
        c_out = CandidateVisualBBox1000(x0=0, y0=0, x1=1005, y1=500)
        self.assertIsNone(validate_visual_bbox1000(c_out))

        c_neg = CandidateVisualBBox1000(x0=-5, y0=0, x1=500, y1=500)
        self.assertIsNone(validate_visual_bbox1000(c_neg))

        # 3. Degenerada devuelve None
        c_deg = CandidateVisualBBox1000(x0=100, y0=100, x1=100, y1=200)
        self.assertIsNone(validate_visual_bbox1000(c_deg))

        # 4. None devuelve None
        self.assertIsNone(validate_visual_bbox1000(None))

        # 5. Válida devuelve VisualBBox1000
        c_val = CandidateVisualBBox1000(x0=30, y0=220, x1=970, y1=270)
        res = validate_visual_bbox1000(c_val)
        self.assertIsInstance(res, VisualBBox1000)
        self.assertEqual(res.x0, 30)
        self.assertEqual(res.y1, 270)

    def test_visual_bbox1000_to_normalized_exact_and_pure(self):
        # 1. Conversión exacta en límites
        b_full = VisualBBox1000(x0=0, y0=0, x1=1000, y1=1000)
        n_full = visual_bbox1000_to_normalized(b_full)
        self.assertIsInstance(n_full, NormalizedBBox)
        self.assertEqual(n_full.x0, 0.0)
        self.assertEqual(n_full.y0, 0.0)
        self.assertEqual(n_full.x1, 1.0)
        self.assertEqual(n_full.y1, 1.0)

        # 2. Conversión exacta en valores intermedios
        b_mid = VisualBBox1000(x0=30, y0=220, x1=970, y1=270)
        n_mid = visual_bbox1000_to_normalized(b_mid)
        self.assertEqual(n_mid.x0, 0.03)
        self.assertEqual(n_mid.y0, 0.22)
        self.assertEqual(n_mid.x1, 0.97)
        self.assertEqual(n_mid.y1, 0.27)

        # 3. Pureza: no muta el objeto de entrada y llamadas repetidas son idénticas
        b_before = b_mid.model_dump()
        n_mid2 = visual_bbox1000_to_normalized(b_mid)
        self.assertEqual(b_mid.model_dump(), b_before)
        self.assertEqual(n_mid.model_dump(), n_mid2.model_dump())

        # 4. Conversión desde CandidateVisualBBox1000 inválido lanza ValueError
        c_inv = CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105)
        with self.assertRaises(ValueError):
            visual_bbox1000_to_normalized(c_inv)


class TestDTOFaultIsolation(unittest.TestCase):
    def test_row_with_inverted_bbox_does_not_crash_page_dto_parsing(self):
        """Verifica que una fila con bbox invertido (y0>=y1) no invalide el DTO de la página ni pierda registros."""
        page_json = """{
            "page_number": 1,
            "rows": [
                {
                    "col": 1,
                    "fil": 26,
                    "especie": "Ah",
                    "altura_cm": 12.5,
                    "bbox": {"x0": 50, "y0": 200, "x1": 950, "y1": 250}
                },
                {
                    "col": 1,
                    "fil": 25,
                    "especie": "Ap",
                    "altura_cm": 8.0,
                    "bbox": {"x0": 180, "y0": 995, "x1": 960, "y1": 105}
                }
            ]
        }"""
        dto = EstadilloPageDTO.model_validate_json(page_json)
        self.assertEqual(len(dto.rows), 2)
        self.assertEqual(dto.rows[0].especie, "Ah")
        self.assertEqual(dto.rows[1].especie, "Ap")

        # Conversión a dominio canónico extrae ambas filas íntegras
        page = dto_to_estadillo_page(dto, page_number=1)
        self.assertEqual(len(page.rows), 2)
        self.assertEqual(page.rows[0].especie.raw, "Ah")
        self.assertEqual(page.rows[1].especie.raw, "Ap")

    def test_collect_dto_regions_preserves_valid_and_discards_invalid(self):
        """Verifica que collect_dto_regions conserve la región válida y descarte la defectuosa."""
        dto = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    especie="Ah",
                    bbox=CandidateVisualBBox1000(x0=50, y0=200, x1=950, y1=250),
                ),
                EstadilloRowDTO(
                    col=1,
                    fil=25,
                    especie="Ap",
                    bbox=CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105),
                ),
            ],
        )
        regions_map = collect_dto_regions([dto])

        # La fila válida (col1_fil26) tiene entrada
        self.assertIn((1, "col1_fil26", "*"), regions_map)
        self.assertEqual(regions_map[(1, "col1_fil26", "*")], NormalizedBBox(x0=0.05, y0=0.2, x1=0.95, y1=0.25))

        # La fila inválida (col1_fil25) fue descartada deterministamente
        self.assertNotIn((1, "col1_fil25", "*"), regions_map)
        self.assertNotIn((1, "col1_fil25", None), regions_map)

    def test_invalid_field_bboxes_and_regions_discarded_without_blocking_data(self):
        """Verifica que field_bboxes y regiones opcionales inválidas se descarten sin afectar a regiones válidas."""
        dto = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(
                objetivo="Test",
                field_bboxes={
                    "objetivo": CandidateVisualBBox1000(x0=50, y0=50, x1=400, y1=100),
                    "fecha": CandidateVisualBBox1000(x0=0, y0=0, x1=1500, y1=50),  # fuera de límites
                },
            ),
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    bbox=CandidateVisualBBox1000(x0=50, y0=200, x1=950, y1=250),
                    field_bboxes={
                        "altura_cm": CandidateVisualBBox1000(x0=400, y0=200, x1=500, y1=250),
                        "especie": CandidateVisualBBox1000(x0=300, y0=250, x1=200, y1=200),  # invertida
                    },
                )
            ],
            regions=[
                VisualRegion1000DTO(
                    page=1,
                    field="altura_cm",
                    row_key="col1_fil26",
                    bbox=CandidateVisualBBox1000(x0=400, y0=200, x1=500, y1=250),
                ),
                VisualRegion1000DTO(
                    page=2,  # discrepancia de página
                    field="especie",
                    row_key="col1_fil26",
                    bbox=CandidateVisualBBox1000(x0=100, y0=100, x1=200, y1=200),
                ),
            ],
        )
        regions_map = collect_dto_regions([dto])

        # Válidas presentes
        self.assertIn((1, None, "objetivo"), regions_map)
        self.assertIn((1, "col1_fil26", "altura_cm"), regions_map)
        self.assertIn((1, "col1_fil26", "*"), regions_map)

        # Inválidas descartadas
        self.assertNotIn((1, None, "fecha"), regions_map)
        self.assertNotIn((1, "col1_fil26", "especie"), regions_map)
        self.assertNotIn((2, "col1_fil26", "especie"), regions_map)

    def test_extract_invalid_region_issues_reports_discard_without_crop(self):
        """Verifica que extract_invalid_region_issues genere ReviewIssues informativos y que match_region_for_issue no les asigne crop."""
        dto = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=25,
                    bbox=CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105),
                )
            ],
        )
        issues = extract_invalid_region_issues([dto])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].warning_code, "INVALID_VISUAL_REGION")
        self.assertEqual(issues[0].row_key, "col1_fil25")
        self.assertIsNone(issues[0].crop_path)
        self.assertIn("995", issues[0].reason)

        # match_region_for_issue debe devolver None para evitar crop fallback
        regions_map = {(1, "col1_fil25", "*"): NormalizedBBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2)}
        matched = match_region_for_issue(issues[0], regions_map)
        self.assertIsNone(matched, "INVALID_VISUAL_REGION jamás debe generar crop ni asociar región")


class TestReviewIssueAggregation(unittest.TestCase):
    def test_derive_row_key_precedence(self):
        # 1. col y fil
        r1 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="26", normalized=26, source_page=1),
            source_page=1,
        )
        self.assertEqual(derive_row_key(r1), "col1_fil26")

        # 2. id solo
        r2 = EstadilloRow(
            id=EvidenceValue[str](raw="43", normalized="43", source_page=1),
            source_page=1,
        )
        self.assertEqual(derive_row_key(r2), "id_43")

        # 3. fil solo
        r3 = EstadilloRow(
            fil=EvidenceValue[int](raw="18", normalized=18, source_page=1),
            source_page=1,
        )
        self.assertEqual(derive_row_key(r3), "fil18")

        # 4. col solo
        r4 = EstadilloRow(
            col=EvidenceValue[int](raw="2", normalized=2, source_page=1),
            source_page=1,
        )
        self.assertEqual(derive_row_key(r4), "col2")

        # 5. Sin coordenadas
        r5 = EstadilloRow(
            especie=EvidenceValue[str](raw="Ap", normalized="P", source_page=1),
            source_page=1,
        )
        self.assertIsNone(derive_row_key(r5))

    def test_extract_issues_preserves_page_none_for_document_warnings(self):
        doc = EstadilloDocument(
            source_file="test.pdf",
            pages=[EstadilloPage(page_number=1)],
            warnings=[
                ExtractionWarning(
                    code="HEADER_CONFLICT",
                    message="Conflicto de cabecera entre páginas",
                    source_page=None,
                    field_name="objetivo",
                )
            ],
        )
        issues = extract_review_issues_from_document(doc)
        self.assertEqual(len(issues), 1)
        iss = issues[0]
        self.assertIsNone(iss.page, "source_page=None no debe inventarse como page=1")
        self.assertEqual(iss.warning_code, "HEADER_CONFLICT")
        self.assertTrue(iss.issue_id.startswith("doc_"))

    def test_extract_issues_from_uncertain_row_evidence(self):
        doc = EstadilloDocument(
            source_file="test.pdf",
            pages=[
                EstadilloPage(
                    page_number=1,
                    rows=[
                        EstadilloRow(
                            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                            fil=EvidenceValue[int](raw="26", normalized=26, source_page=1),
                            especie=EvidenceValue[str](
                                raw="Mz",
                                normalized=None,
                                source_page=1,
                                uncertain=True,
                                alternatives=["M", "Mz"],
                            ),
                            source_page=1,
                        )
                    ],
                )
            ],
        )
        issues = extract_review_issues_from_document(doc)
        self.assertEqual(len(issues), 1)
        iss = issues[0]
        self.assertEqual(iss.page, 1)
        self.assertEqual(iss.field, "especie")
        self.assertEqual(iss.row_key, "col1_fil26")
        self.assertEqual(iss.candidates, ["M", "Mz"])
        self.assertIn("Mz", iss.reason)

    def test_extract_issues_from_uncertain_header_evidence(self):
        doc = EstadilloDocument(
            source_file="test.pdf",
            header=EstadilloDocHeader(
                objetivo=EvidenceValue[str](
                    raw="???",
                    normalized=None,
                    source_page=1,
                    uncertain=True,
                    alternatives=["Ensayo 1", "Ensayo 2"],
                )
            ),
            pages=[EstadilloPage(page_number=1)],
        )
        issues = extract_review_issues_from_document(doc)
        self.assertEqual(len(issues), 1)
        iss = issues[0]
        self.assertEqual(iss.page, 1)
        self.assertEqual(iss.field, "objetivo")
        self.assertIsNone(iss.row_key)
        self.assertEqual(iss.candidates, ["Ensayo 1", "Ensayo 2"])

    def test_extract_issues_from_extraction_warnings(self):
        doc = EstadilloDocument(
            source_file="test.pdf",
            pages=[EstadilloPage(page_number=1)],
            warnings=[
                ExtractionWarning(
                    code="UNRECOGNIZED_SPECIES",
                    message="Especie no reconocida: 'M2'",
                    source_page=1,
                    field_name="especie",
                    details={"raw": "M2", "col": 1, "fil": 15},
                ),
                ExtractionWarning(
                    code="INVALID_BBCH_FORMAT",
                    message="BBCH '245' no cumple formato",
                    source_page=1,
                    field_name="bbch",
                    details={"raw": "245", "col": 1, "fil": 15},
                ),
            ],
        )
        issues = extract_review_issues_from_document(doc)
        self.assertEqual(len(issues), 2)
        codes = [i.warning_code for i in issues]
        self.assertIn("UNRECOGNIZED_SPECIES", codes)
        self.assertIn("INVALID_BBCH_FORMAT", codes)
        self.assertEqual(issues[0].row_key, "col1_fil15")

    def test_extract_issues_deduplication_and_deterministic_order(self):
        # Mismo warning duplicado
        w = ExtractionWarning(
            code="DUPLICATE_COORDINATES",
            message="Coordenadas duplicadas (col=1, fil=10)",
            source_page=1,
            field_name="col,fil",
            details={"col": 1, "fil": 10},
        )
        doc = EstadilloDocument(
            source_file="test.pdf",
            pages=[EstadilloPage(page_number=1)],
            warnings=[w, w],
        )
        issues = extract_review_issues_from_document(doc)
        self.assertEqual(len(issues), 1, "Los issues idénticos deben deduplicarse")

    def test_aggregation_purity_and_idempotence(self):
        doc = EstadilloDocument(
            source_file="test.pdf",
            pages=[
                EstadilloPage(
                    page_number=1,
                    rows=[
                        EstadilloRow(
                            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                            especie=EvidenceValue[str](raw="X", source_page=1, uncertain=True),
                            source_page=1,
                        )
                    ],
                )
            ],
        )
        doc_json_before = doc.model_dump_json()
        issues_run1 = extract_review_issues_from_document(doc)
        issues_run2 = extract_review_issues_from_document(doc)
        doc_json_after = doc.model_dump_json()

        self.assertEqual(doc_json_before, doc_json_after, "El documento original no debe ser mutado")
        self.assertEqual([i.model_dump() for i in issues_run1], [i.model_dump() for i in issues_run2])


class TestCropsGeneration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_crops_"))
        # Crear una imagen sintética de prueba 200x200 PNG
        self.test_img_path = self.temp_dir / "test_page.png"
        img = Image.new("RGB", (200, 200), color=(255, 0, 0))
        img.save(str(self.test_img_path), format="PNG")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_validate_safe_crop_path_confinement_and_traversal_rejection(self):
        allowed = self.temp_dir / "review"
        allowed.mkdir(parents=True, exist_ok=True)

        valid_path = allowed / "crop1.png"
        res = validate_safe_crop_path(valid_path, allowed)
        self.assertEqual(res, valid_path.resolve())

        # Escape de directorio
        invalid_path = self.temp_dir / "outside.png"
        with self.assertRaises(ValueError):
            validate_safe_crop_path(invalid_path, allowed)

    def test_crop_image_region_creates_valid_png_and_decodes(self):
        out_path = self.temp_dir / "review" / "crop_p1.png"
        bbox = NormalizedBBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5)

        res_path = crop_image_region(
            image_path=self.test_img_path,
            bbox=bbox,
            output_path=out_path,
            allowed_dir=self.temp_dir,
        )
        self.assertTrue(res_path.is_file())

        with Image.open(res_path) as cropped:
            self.assertEqual(cropped.format, "PNG")
            # 200 * 0.4 = 80px
            self.assertEqual(cropped.size, (80, 80))

    def test_crop_image_region_degenerate_bbox_raises(self):
        out_path = self.temp_dir / "review" / "crop_fail.png"
        # Bbox con ancho 0 en píxeles (0.1 a 0.1001 en 200px da 0px)
        bbox = NormalizedBBox(x0=0.1000, y0=0.1, x1=0.1001, y1=0.5)
        with self.assertRaises(ValueError):
            crop_image_region(
                image_path=self.test_img_path,
                bbox=bbox,
                output_path=out_path,
                allowed_dir=self.temp_dir,
                min_size_px=2,
            )

    def test_generate_review_crops_validates_review_dir_confinement(self):
        canonical_base = self.temp_dir / "doc"
        canonical_base.mkdir(parents=True, exist_ok=True)
        escaped_review_dir = self.temp_dir / "escaped_review"

        issue = ReviewIssue(issue_id="iss1", page=1, field="f", reason="r")
        with self.assertRaises(ValueError):
            generate_review_crops_for_issues(
                issues=[issue],
                page_images={1: self.test_img_path},
                regions_map={},
                review_dir=escaped_review_dir,
                canonical_base_dir=canonical_base,
            )

    def test_generate_review_crops_is_non_mutating(self):
        review_dir = self.temp_dir / "review"
        canonical_base = self.temp_dir

        issue_orig = ReviewIssue(
            issue_id="iss_orig",
            page=1,
            field="altura_cm",
            row_key="col1_fil26",
            reason="Duda",
            crop_path=None,
        )
        input_list = [issue_orig]

        regions_map = {
            (1, "col1_fil26", "altura_cm"): NormalizedBBox(x0=0.1, y0=0.1, x1=0.4, y1=0.4)
        }
        page_images = {1: self.test_img_path}

        updated_issues = generate_review_crops_for_issues(
            issues=input_list,
            page_images=page_images,
            regions_map=regions_map,
            review_dir=review_dir,
            canonical_base_dir=canonical_base,
        )

        self.assertIsNot(updated_issues, input_list, "Debe retornar una nueva lista")
        self.assertIsNot(updated_issues[0], issue_orig, "Debe retornar copias de los objetos ReviewIssue")
        self.assertIsNone(issue_orig.crop_path, "El objeto original no debe haber sido mutado")
        self.assertEqual(updated_issues[0].crop_path, "review/p001_col1_fil26_altura_cm.png")

    def test_io_failure_during_crop_generation_propagates_exception(self):
        review_dir = self.temp_dir / "review"
        canonical_base = self.temp_dir

        issue = ReviewIssue(
            issue_id="iss_fail",
            page=1,
            field="altura_cm",
            row_key="col1_fil26",
            reason="Duda",
        )
        regions_map = {
            (1, "col1_fil26", "altura_cm"): NormalizedBBox(x0=0.1, y0=0.1, x1=0.4, y1=0.4)
        }
        page_images = {1: self.test_img_path}

        with patch("os.replace", side_effect=PermissionError("Acceso denegado al disco")):
            with self.assertRaises(PermissionError):
                generate_review_crops_for_issues(
                    issues=[issue],
                    page_images=page_images,
                    regions_map=regions_map,
                    review_dir=review_dir,
                    canonical_base_dir=canonical_base,
                )

    def test_generate_review_crops_for_issues_matching_and_page_none_fallback(self):
        review_dir = self.temp_dir / "review"
        canonical_base = self.temp_dir

        issue_with_crop = ReviewIssue(
            issue_id="iss_crop_1",
            page=1,
            field="altura_cm",
            row_key="col1_fil26",
            reason="Altura dudosa",
        )
        issue_no_crop = ReviewIssue(
            issue_id="iss_crop_2",
            page=1,
            field="bbch",
            row_key="col1_fil27",
            reason="BBCH dudoso",
        )
        issue_page_none = ReviewIssue(
            issue_id="iss_doc",
            page=None,
            field="document",
            reason="Warning global",
        )

        regions_map = {
            (1, "col1_fil26", "altura_cm"): NormalizedBBox(x0=0.2, y0=0.2, x1=0.4, y1=0.4)
        }
        page_images = {1: self.test_img_path}

        updated_issues = generate_review_crops_for_issues(
            issues=[issue_with_crop, issue_no_crop, issue_page_none],
            page_images=page_images,
            regions_map=regions_map,
            review_dir=review_dir,
            canonical_base_dir=canonical_base,
        )

        self.assertIsNotNone(updated_issues[0].crop_path)
        self.assertTrue(updated_issues[0].crop_path.startswith("review/"))
        self.assertTrue((canonical_base / updated_issues[0].crop_path).is_file())

        self.assertIsNone(updated_issues[1].crop_path)
        self.assertIsNone(updated_issues[2].crop_path, "Issue con page=None debe tener crop_path=None")


class TestEstadilloProfileReviewIntegration(unittest.TestCase):
    def test_profile_creates_review_layout_and_empty_issues_json_when_clean(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_profile_clean_"))
        temp_input = temp_base / "sample.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (100, 100), color=(255, 255, 255)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # Página limpia sin incertidumbres
        clean_page_dto = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Ensayo"),
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    especie="Ah",
                    altura_cm=10.5,
                    bbch="22",
                    bbox=VisualBBox1000(x0=30, y0=220, x1=970, y1=270),
                )
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = clean_page_dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            canonical_dir = temp_base / "sample"
            self.assertTrue(canonical_dir.is_dir())
            review_dir = canonical_dir / "review"
            self.assertTrue(review_dir.is_dir())

            issues_file = review_dir / "issues.json"
            self.assertTrue(issues_file.is_file())

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            self.assertEqual(issues, [], "Un documento sin incidencias debe generar review/issues.json con lista vacía")
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_profile_creates_review_issues_and_crops_for_uncertain_regions(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_profile_issues_"))
        temp_input = temp_base / "sample_issues.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(128, 128, 128)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # Página con campo incierto y región visual en DTO usando VisualBBox1000
        row_dto = EstadilloRowDTO(
            col=1,
            fil=26,
            especie="Ah",
            altura_cm=15.0,
            uncertain_fields=["altura_cm"],
            bbox=VisualBBox1000(x0=50, y0=200, x1=950, y1=250),
            field_bboxes={
                "altura_cm": VisualBBox1000(x0=300, y0=300, x1=600, y1=600)
            },
        )
        page_dto = EstadilloPageDTO(
            page_number=1,
            header=None,
            rows=[row_dto],
        )
        mock_agent.ask_page_vision_structured.return_value = page_dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            canonical_dir = temp_base / "sample_issues"
            issues_file = canonical_dir / "review" / "issues.json"
            self.assertTrue(issues_file.is_file())

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].field, "altura_cm")
            self.assertIsNotNone(issues[0].crop_path)
            self.assertTrue(issues[0].crop_path.startswith("review/"))

            crop_abs_path = canonical_dir / issues[0].crop_path
            self.assertTrue(crop_abs_path.is_file())
            with Image.open(crop_abs_path) as cimg:
                self.assertEqual(cimg.format, "PNG")
                self.assertEqual(cimg.size, (60, 60))
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_profile_handles_inverted_bbox_fault_isolation_e2e(self):
        """Verifica que el perfil procese e2e con aislamiento de fallos: extrae todas las filas, reporta INVALID_VISUAL_REGION y no falla."""
        temp_base = Path(tempfile.mkdtemp(prefix="test_profile_fault_iso_"))
        temp_input = temp_base / "sample_fault.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(128, 128, 128)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # Fila 1: válida con incertidumbre en especie y bbox válido
        row1 = EstadilloRowDTO(
            col=1,
            fil=26,
            especie="Ah",
            altura_cm=10.0,
            uncertain_fields=["especie"],
            bbox=CandidateVisualBBox1000(x0=50, y0=200, x1=950, y1=250),
            field_bboxes={"especie": CandidateVisualBBox1000(x0=100, y0=200, x1=300, y1=250)},
        )
        # Fila 2: válida en datos, pero con bbox invertido y0=995 >= y1=105
        row2 = EstadilloRowDTO(
            col=1,
            fil=25,
            especie="Ap",
            altura_cm=15.0,
            bbox=CandidateVisualBBox1000(x0=180, y0=995, x1=960, y1=105),
        )

        page_dto = EstadilloPageDTO(page_number=1, rows=[row1, row2])
        mock_agent.ask_page_vision_structured.return_value = page_dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            # 1. Ambos registros se extraen completamente
            self.assertEqual(doc_res.total_records, 2)
            self.assertEqual(len(doc_res.pages[0].rows), 2)

            canonical_dir = temp_base / "sample_fault"
            issues_file = canonical_dir / "review" / "issues.json"
            self.assertTrue(issues_file.is_file())

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            # Debe contener UNCERTAIN_EVIDENCE para row1 y INVALID_VISUAL_REGION para row2
            codes = [i.warning_code for i in issues]
            self.assertIn("UNCERTAIN_EVIDENCE", codes)
            self.assertIn("INVALID_VISUAL_REGION", codes)

            # Row 1 tiene crop válido
            row1_issue = next(i for i in issues if i.warning_code == "UNCERTAIN_EVIDENCE")
            self.assertIsNotNone(row1_issue.crop_path)
            self.assertTrue((canonical_dir / row1_issue.crop_path).is_file())

            # Row 2 tiene crop_path = None (sin crop)
            row2_issue = next(i for i in issues if i.warning_code == "INVALID_VISUAL_REGION")
            self.assertIsNone(row2_issue.crop_path)

            # Cero PNGs huérfanos
            actual_pngs = set(p.resolve() for p in (canonical_dir / "review").glob("*.png"))
            referenced_pngs = {
                (canonical_dir / iss.crop_path).resolve()
                for iss in issues
                if iss.crop_path is not None
            }
            self.assertEqual(actual_pngs, referenced_pngs)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_profile_crop_io_failure_triggers_rollback_and_preserves_canonical(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_rollback_crop_fail_"))
        temp_input = temp_base / "doc_fail.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        canonical_dir = temp_base / "doc_fail"
        canonical_dir.mkdir(parents=True)
        notebook_path = canonical_dir / "notebook.md"
        notebook_path.write_text("CANONICAL VERSION ANTERIOR", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (100, 100), color=(50, 50, 50)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        dto = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    especie="Ah",
                    uncertain_fields=["especie"],
                    field_bboxes={"especie": VisualBBox1000(x0=100, y0=100, x1=300, y1=300)},
                )
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            with patch("PIL.Image.Image.save", side_effect=OSError("Disk write error")):
                with self.assertRaises(OSError):
                    profile.run(temp_input)

            self.assertTrue(canonical_dir.exists())
            self.assertEqual(notebook_path.read_text(encoding="utf-8"), "CANONICAL VERSION ANTERIOR")

            self.assertEqual(len(list(temp_base.glob(".staging_*"))), 0)
            self.assertEqual(len(list(temp_base.glob(".backup_*"))), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_profile_republication_removes_obsolete_crops(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_profile_repub_"))
        temp_input = temp_base / "doc.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(100, 100, 100)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # 1. Primera ejecución con 1 issue con crop
        dto1 = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    especie="Ah",
                    uncertain_fields=["especie"],
                    field_bboxes={"especie": VisualBBox1000(x0=100, y0=100, x1=300, y1=300)},
                )
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = dto1
        profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
        profile.run(temp_input)

        canonical_dir = temp_base / "doc"
        png_files_1 = list((canonical_dir / "review").glob("*.png"))
        self.assertEqual(len(png_files_1), 1)

        # 2. Segunda ejecución limpia (0 issues)
        dto2 = EstadilloPageDTO(
            page_number=1,
            rows=[EstadilloRowDTO(col=1, fil=26, especie="Ah", bbox=VisualBBox1000(x0=50, y0=200, x1=950, y1=250))],
        )
        mock_agent.ask_page_vision_structured.return_value = dto2
        profile.run(temp_input)

        png_files_2 = list((canonical_dir / "review").glob("*.png"))
        self.assertEqual(len(png_files_2), 0, "Los crops obsoletos de la ejecución previa deben eliminarse")
        issues_2 = review_issues_from_json((canonical_dir / "review" / "issues.json").read_text(encoding="utf-8"))
        self.assertEqual(issues_2, [])
        shutil.rmtree(temp_base, ignore_errors=True)

    def test_prompt_template_requires_row_bbox_and_compact_format(self):
        """Verifica que el prompt para el VLM exija contractualmente bbox de fila y field_bboxes solo para celdas dudosas."""
        from src.fieldnotes.profiles.estadillo import ESTADILLO_PROMPT_TEMPLATE

        prompt = ESTADILLO_PROMPT_TEMPLATE.format(page_number=1)
        self.assertIn("'bbox'", prompt)
        self.assertIn("CADA fila extraída", prompt)
        self.assertIn("'field_bboxes'", prompt)
        self.assertIn("ÚNICAMENTE si una celda concreta es dudosa", prompt)
        self.assertIn("EstadilloPageDTO", prompt)

    def test_prompt_template_bbox_anchored_to_full_image_dimensions(self):
        """Verifica que el prompt exija bbox en cuadrícula entera 0..1000 relativo a las dimensiones TOTALES de la imagen completa."""
        from src.fieldnotes.profiles.estadillo import ESTADILLO_PROMPT_TEMPLATE

        prompt = ESTADILLO_PROMPT_TEMPLATE.format(page_number=1)

        # El prompt debe declarar explícitamente que las coordenadas son relativas a la imagen total en cuadrícula 0..1000
        self.assertIn("dimensiones TOTALES de la imagen completa", prompt)
        self.assertIn("cuadrícula 0..1000", prompt)
        self.assertIn("0=borde superior/izquierdo", prompt)
        self.assertIn("1000=borde inferior/derecho", prompt)

        # El prompt debe prohibir explícitamente decimales, negativos y >1000
        self.assertIn("NUNCA generar números decimales, negativos ni coordenadas mayores a 1000", prompt)
        self.assertIn("ENTEROS en [0, 1000]", prompt)

        # El prompt debe incluir ejemplos numéricos válidos en cuadrícula 0..1000
        self.assertIn("fila1={x0:", prompt)
        self.assertIn("fila10={x0:", prompt)
        self.assertIn("fila20={x0:", prompt)

        # La instrucción bbox debe ser OBLIGATORIA
        self.assertIn("OBLIGATORIO", prompt)

    def test_prompt_template_bbox_example_values_are_valid(self):
        """Verifica que los valores numéricos del ejemplo en el prompt sean coordenadas bbox válidas en [0, 1000]."""
        from src.fieldnotes.profiles.estadillo import ESTADILLO_PROMPT_TEMPLATE

        prompt = ESTADILLO_PROMPT_TEMPLATE.format(page_number=1)

        # Extraer pares key:value del ejemplo en el prompt (e.g. x0:30,y0:220,x1:970,y1:270)
        pattern = r'x0:(\d+),y0:(\d+),x1:(\d+),y1:(\d+)'
        matches = re.findall(pattern, prompt)
        self.assertGreater(len(matches), 0, "El prompt debe contener al menos un ejemplo numérico de bbox")

        for x0s, y0s, x1s, y1s in matches:
            x0, y0, x1, y1 = int(x0s), int(y0s), int(x1s), int(y1s)
            try:
                bbox1000 = VisualBBox1000(x0=x0, y0=y0, x1=x1, y1=y1)
                norm_bbox = visual_bbox1000_to_normalized(bbox1000)
                self.assertIsInstance(norm_bbox, NormalizedBBox)
            except (ValidationError, ValueError) as e:
                self.fail(
                    f"El ejemplo bbox en el prompt contiene coordenadas inválidas: "
                    f"{{x0:{x0},y0:{y0},x1:{x1},y1:{y1}}} → {e}"
                )

    def test_warning_with_row_key_matches_row_bbox_and_generates_crop(self):
        """Verifica que advertencias asociadas a una fila (con row_key) hereden el bbox de la fila y generen crop."""
        temp_base = Path(tempfile.mkdtemp(prefix="test_warn_crop_"))
        temp_input = temp_base / "doc_warn.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(150, 150, 150)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # Fila con especie no reconocida 'M2' y bbox de fila en cuadrícula 1000
        page_dto = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=15,
                    especie="M2",
                    altura_cm=8.0,
                    bbch="22",
                    bbox=VisualBBox1000(x0=100, y0=300, x1=900, y1=350),
                )
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = page_dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            canonical_dir = temp_base / "doc_warn"
            issues_file = canonical_dir / "review" / "issues.json"
            self.assertTrue(issues_file.is_file())

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].row_key, "col1_fil15")
            self.assertEqual(issues[0].warning_code, "UNRECOGNIZED_SPECIES")
            self.assertIsNotNone(issues[0].crop_path)
            self.assertTrue(issues[0].crop_path.startswith("review/"))

            crop_file = canonical_dir / issues[0].crop_path
            self.assertTrue(crop_file.is_file())
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_document_level_warning_without_row_key_does_not_match_arbitrary_row(self):
        """Verifica que advertencias globales/documentales (page=None o sin row_key) no se asignen a una fila ni generen crop erróneo."""
        temp_base = Path(tempfile.mkdtemp(prefix="test_doc_warn_"))
        temp_input = temp_base / "doc_global.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(150, 150, 150)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        img2 = temp_base / "page_002.png"
        Image.new("RGB", (200, 200), color=(150, 150, 150)).save(str(img2), format="PNG")
        art2 = PageArtifact(page_number=2, image_path=img2, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1, art2]

        # Dos páginas con objetivos contradictorios para generar HEADER_CONFLICT a nivel documental
        dto1 = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Ensayo Alfa"),
            rows=[EstadilloRowDTO(col=1, fil=26, especie="Ah", bbox=VisualBBox1000(x0=100, y0=200, x1=900, y1=250))],
        )
        dto2 = EstadilloPageDTO(
            page_number=2,
            header=EstadilloHeaderDTO(objetivo="Ensayo Beta"),
            rows=[EstadilloRowDTO(col=1, fil=25, especie="Ah", bbox=VisualBBox1000(x0=100, y0=200, x1=900, y1=250))],
        )
        mock_agent.ask_page_vision_structured.side_effect = [dto1, dto2]

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            canonical_dir = temp_base / "doc_global"
            issues_file = canonical_dir / "review" / "issues.json"
            self.assertTrue(issues_file.is_file())

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            header_conflicts = [i for i in issues if i.warning_code == "HEADER_CONFLICT"]
            self.assertEqual(len(header_conflicts), 1)
            self.assertEqual(header_conflicts[0].page, 1)
            self.assertIsNone(header_conflicts[0].row_key)
            self.assertIsNone(header_conflicts[0].crop_path, "Warning global no debe generar crop ni asociarse a fila arbitraria")
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_unreferenced_pngs_count_is_zero_in_mocked_profile(self):
        """Verifica que el número de archivos PNG no referenciados en review/ sea exactamente cero."""
        temp_base = Path(tempfile.mkdtemp(prefix="test_unref_pngs_"))
        temp_input = temp_base / "doc_clean_crops.pdf"
        temp_input.write_text("fake pdf", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")
        os.makedirs(Path(mock_agent.output_dir) / "raw", exist_ok=True)

        img1 = temp_base / "page_001.png"
        Image.new("RGB", (200, 200), color=(100, 100, 100)).save(str(img1), format="PNG")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        # 1 fila con incertidumbre y bbox, 1 fila limpia
        dto = EstadilloPageDTO(
            page_number=1,
            rows=[
                EstadilloRowDTO(
                    col=1,
                    fil=26,
                    especie="Ah",
                    altura_cm=12.0,
                    uncertain_fields=["altura_cm"],
                    bbox=VisualBBox1000(x0=50, y0=200, x1=950, y1=250),
                    field_bboxes={"altura_cm": VisualBBox1000(x0=300, y0=300, x1=500, y1=400)},
                ),
                EstadilloRowDTO(
                    col=1,
                    fil=25,
                    especie="Ap",
                    altura_cm=10.0,
                    bbox=VisualBBox1000(x0=50, y0=250, x1=950, y1=300),
                ),
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            canonical_dir = temp_base / "doc_clean_crops"
            review_dir = canonical_dir / "review"
            issues_file = review_dir / "issues.json"

            issues = review_issues_from_json(issues_file.read_text(encoding="utf-8"))
            referenced_pngs = {
                (canonical_dir / iss.crop_path).resolve()
                for iss in issues
                if iss.crop_path is not None
            }
            actual_pngs = set(p.resolve() for p in review_dir.glob("*.png"))

            self.assertEqual(len(referenced_pngs), 1)
            self.assertEqual(actual_pngs, referenced_pngs)
            self.assertEqual(len(actual_pngs - referenced_pngs), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)


class TestRealEstadilloReviewIntegration(unittest.TestCase):
    def test_real_estadillo_review_e2e_on_pdf(self):
        """Prueba opt-in de integración real E2E con 26-05-06.pdf verificando review/issues.json."""
        flag = os.environ.get("RUN_ESTADILLO_INTEGRATION")
        if flag != "1":
            self.skipTest(
                "Prueba E2E real del perfil estadillo desactivada por defecto; "
                "requiere RUN_ESTADILLO_INTEGRATION=1"
            )

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        model_name = os.environ.get("LM_STUDIO_VISION_MODEL", "qwen/qwen3.5-9b")
        pdf_path = Path("26-05-06.pdf")
        if not pdf_path.is_file():
            self.fail(f"Archivo '{pdf_path}' no encontrado.")

        temp_output = tempfile.mkdtemp(prefix="test_e2e_review_")
        try:
            agent = UnlimitedOCRAgent(
                vision_model=model_name,
                output_dir=temp_output,
                ocr_mode="worker",
            )
            md_res, doc_res = agent.process_estadillo(
                file_path=pdf_path,
                output_dir=temp_output,
                max_tokens=4096,
            )

            # 1. Total records >= 50
            self.assertGreaterEqual(doc_res.total_records, 50, "El documento real debe extraer >= 50 registros")

            canonical_dir = Path(temp_output) / "26-05-06"
            review_dir = canonical_dir / "review"
            self.assertTrue(review_dir.is_dir())

            issues_path = review_dir / "issues.json"
            self.assertTrue(issues_path.is_file())

            issues = review_issues_from_json(issues_path.read_text(encoding="utf-8"))
            self.assertIsInstance(issues, list)

            # 2. len(issues) > 0 y crops_found > 0
            self.assertGreater(len(issues), 0, "El documento real debe reportar al menos un issue de revisión")
            crops_found = [i for i in issues if i.crop_path is not None]
            self.assertGreater(len(crops_found), 0, "Debe haberse generado al menos un crop visual para los issues con región")

            # 3. Todos los crop_path referenciados existen y son PNG válidos
            referenced_png_paths = set()
            for iss in issues:
                if iss.crop_path is not None:
                    self.assertTrue(iss.crop_path.startswith("review/"))
                    crop_file = canonical_dir / iss.crop_path
                    self.assertTrue(crop_file.is_file(), f"Crop referenciado no encontrado: {crop_file}")
                    referenced_png_paths.add(crop_file.resolve())
                    with Image.open(crop_file) as cimg:
                        self.assertEqual(cimg.format, "PNG")

            # 4. Cero PNGs huérfanos o no referenciados en review/
            actual_pngs = set(p.resolve() for p in review_dir.glob("*.png"))
            unreferenced = actual_pngs - referenced_png_paths
            self.assertEqual(len(unreferenced), 0, f"Se encontraron {len(unreferenced)} PNGs huérfanos no referenciados: {unreferenced}")

            print(f"\nREAL_ESTADILLO_REVIEW_E2E: total_records={doc_res.total_records} issues={len(issues)} crops_found={len(crops_found)} unreferenced_pngs=0")
        finally:
            shutil.rmtree(temp_output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()