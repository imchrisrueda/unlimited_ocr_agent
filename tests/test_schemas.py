import unittest
import pydantic
from pathlib import Path
from pydantic import ValidationError

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.schemas.document import BlockIR, PageIR, DocumentIR
from src.fieldnotes.schemas.estadillo import (
    EstadilloHeader,
    EstadilloRow,
    EstadilloPage,
    EstadilloDocument,
)


class TestPydanticVersion(unittest.TestCase):
    def test_pydantic_v2_installed(self):
        """Verifica que Pydantic v2 esté instalado y que no se use v1."""
        self.assertTrue(
            pydantic.__version__.startswith("2."),
            f"Se requiere Pydantic v2.x, versión detectada: {pydantic.__version__}",
        )


class TestEvidenceValueStrictValidation(unittest.TestCase):
    def test_evidence_valid_instantiation(self):
        ev = EvidenceValue[str](
            raw="Quercus robur",
            normalized="Q. robur",
            source_page=1,
            uncertain=False,
        )
        self.assertEqual(ev.raw, "Quercus robur")
        self.assertEqual(ev.normalized, "Q. robur")
        self.assertEqual(ev.source_page, 1)
        self.assertFalse(ev.uncertain)
        self.assertEqual(ev.alternatives, [])

    def test_evidence_strict_rejection_of_string_for_int_source_page(self):
        with self.assertRaises(ValidationError):
            EvidenceValue[str](raw="test", source_page="1")  # type: ignore

    def test_evidence_strict_rejection_of_string_for_float_normalized(self):
        with self.assertRaises(ValidationError):
            EvidenceValue[float](raw="10.5", normalized="10.5", source_page=1)  # type: ignore

    def test_evidence_strict_rejection_of_string_for_bool_uncertain(self):
        with self.assertRaises(ValidationError):
            EvidenceValue[str](raw="test", source_page=1, uncertain="false")  # type: ignore

    def test_evidence_json_numbers_validate_correctly(self):
        ev = EvidenceValue[float].model_validate_json(
            '{"raw": "10.5", "normalized": 10.5, "source_page": 1, "uncertain": false}'
        )
        self.assertEqual(ev.normalized, 10.5)
        self.assertEqual(ev.source_page, 1)

    def test_evidence_structural_invariants(self):
        # Ambos None con uncertain=False debe fallar
        with self.assertRaises(ValidationError) as ctx:
            EvidenceValue[str](source_page=1, uncertain=False)
        self.assertIn("al menos 'raw' o 'normalized'", str(ctx.exception))

        # Ambos None con uncertain=True (evidencia ilegible) debe ser válido
        ev_illegible = EvidenceValue[str](source_page=1, uncertain=True)
        self.assertIsNone(ev_illegible.raw)
        self.assertIsNone(ev_illegible.normalized)
        self.assertTrue(ev_illegible.uncertain)

        # alternatives con uncertain=False debe fallar
        with self.assertRaises(ValidationError) as ctx:
            EvidenceValue[str](
                raw="Pinus",
                source_page=1,
                uncertain=False,
                alternatives=["Quercus"],
            )
        self.assertIn("alternatives", str(ctx.exception).lower())

        # alternatives con uncertain=True debe ser válido
        ev_alt = EvidenceValue[str](
            raw="P?",
            source_page=1,
            uncertain=True,
            alternatives=["Pinus", "Populus"],
        )
        self.assertEqual(ev_alt.alternatives, ["Pinus", "Populus"])

    def test_evidence_source_page_must_be_ge_1(self):
        with self.assertRaises(ValidationError):
            EvidenceValue[str](raw="test", source_page=0)
        with self.assertRaises(ValidationError):
            EvidenceValue[str](raw="test", source_page=-1)

    def test_evidence_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            EvidenceValue[str](raw="test", source_page=1, extra_field="forbidden")  # type: ignore

    def test_evidence_serialization_roundtrip(self):
        ev = EvidenceValue[float](
            raw="12.5 cm",
            normalized=12.5,
            source_page=3,
            uncertain=True,
            alternatives=["12.3", "12.8"],
        )
        json_str = ev.model_dump_json()
        restored = EvidenceValue[float].model_validate_json(json_str)
        self.assertEqual(ev, restored)


class TestExtractionWarningSchema(unittest.TestCase):
    def test_warning_valid(self):
        w = ExtractionWarning(
            code="ILLEGIBLE_CELL",
            message="No se puede leer la altura en fila 4",
            severity="warning",
            source_page=1,
            field_name="altura_cm",
            details={"col": 3, "fil": 4, "raw_snippet": "???"},
        )
        self.assertEqual(w.code, "ILLEGIBLE_CELL")
        self.assertEqual(w.severity, "warning")
        self.assertEqual(w.source_page, 1)

    def test_warning_strict_source_page_type(self):
        with self.assertRaises(ValidationError):
            ExtractionWarning(code="W01", message="msg", source_page="1")  # type: ignore

    def test_warning_invalid_severity(self):
        with self.assertRaises(ValidationError):
            ExtractionWarning(
                code="ERR",
                message="msg",
                severity="critical",  # type: ignore
            )

    def test_warning_invalid_source_page(self):
        with self.assertRaises(ValidationError):
            ExtractionWarning(
                code="ERR",
                message="msg",
                source_page=0,
            )

    def test_warning_extra_forbidden(self):
        with self.assertRaises(ValidationError):
            ExtractionWarning(
                code="ERR",
                message="msg",
                unknown_key=123,  # type: ignore
            )


class TestDocumentIRSchemas(unittest.TestCase):
    def test_block_ir_valid(self):
        block = BlockIR(
            block_type="table",
            content={"rows": 5, "cols": 8},
            source_page=1,
            raw_text="| id | col | fil |",
        )
        self.assertEqual(block.block_type, "table")
        self.assertEqual(block.source_page, 1)

    def test_page_ir_valid(self):
        page = PageIR(
            page_number=1,
            image_path=Path("pages/page_001.png"),
            raw_ocr="texto ocr",
            blocks=[
                BlockIR(block_type="text", source_page=1, raw_text="Cabecera"),
            ],
        )
        self.assertEqual(page.page_number, 1)
        self.assertEqual(len(page.blocks), 1)

    def test_page_ir_block_source_page_mismatch_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            PageIR(
                page_number=1,
                blocks=[
                    BlockIR(block_type="text", source_page=2, raw_text="Mismatch"),
                ],
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_page_ir_warning_source_page_mismatch_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            PageIR(
                page_number=1,
                warnings=[
                    ExtractionWarning(code="W01", message="msg", source_page=2),
                ],
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_page_ir_page_number_ge_1(self):
        with self.assertRaises(ValidationError):
            PageIR(page_number=0)

    def test_document_ir_unique_and_sorted_pages(self):
        p2 = PageIR(page_number=2)
        p1 = PageIR(page_number=1)
        doc = DocumentIR(
            source_file="doc.pdf",
            pages=[p2, p1],
            metadata={"total_pages": 2},
        )
        self.assertEqual([p.page_number for p in doc.pages], [1, 2])

    def test_document_ir_duplicate_page_number_rejected(self):
        p1_a = PageIR(page_number=1)
        p1_b = PageIR(page_number=1)
        with self.assertRaises(ValidationError) as ctx:
            DocumentIR(
                source_file="doc.pdf",
                pages=[p1_a, p1_b],
            )
        self.assertIn("duplicado", str(ctx.exception).lower())

    def test_document_ir_extra_forbidden(self):
        with self.assertRaises(ValidationError):
            DocumentIR(
                source_file="doc.pdf",
                extra_prop=True,  # type: ignore
            )

    def test_document_ir_roundtrip(self):
        doc = DocumentIR(
            source_file="sample.pdf",
            pages=[
                PageIR(
                    page_number=1,
                    blocks=[BlockIR(block_type="table", source_page=1, content={"cols": 8})],
                )
            ],
            metadata={"author": "Field Agent", "version": 1},
            warnings=[ExtractionWarning(code="W01", message="Warning 1", source_page=1)],
        )
        dumped = doc.model_dump_json()
        loaded = DocumentIR.model_validate_json(dumped)
        self.assertEqual(doc, loaded)


class TestEstadilloSchemas(unittest.TestCase):
    def test_estadillo_header_valid(self):
        header = EstadilloHeader(
            objetivo=EvidenceValue[str](raw="Muestreo de parcelas", source_page=1),
            fecha=EvidenceValue[str](raw="2026-05-06", source_page=1),
            asistentes=EvidenceValue[str](raw="C. Rueda, M. Perez", source_page=1),
            equipamiento=EvidenceValue[str](raw="Cinta métrica, GPS", source_page=1),
            situacion_atmosferica=EvidenceValue[str](raw="Despejado", source_page=1),
            especies_declaradas=EvidenceValue[str](raw="P;H;R;M", source_page=1),
            source_page=1,
        )
        self.assertEqual(header.objetivo.raw, "Muestreo de parcelas")
        self.assertEqual(header.source_page, 1)

    def test_estadillo_header_mismatched_evidence_source_page_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            EstadilloHeader(
                objetivo=EvidenceValue[str](raw="Muestreo", source_page=2),  # Discrepa con source_page=1
                source_page=1,
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_estadillo_row_valid(self):
        row = EstadilloRow(
            id=EvidenceValue[str](raw="R001", normalized="R001", source_page=1),
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            especie=EvidenceValue[str](
                raw="Pinus sylvestris",
                normalized="P",
                source_page=1,
            ),
            altura_cm=EvidenceValue[float](
                raw="145",
                normalized=145.0,
                source_page=1,
            ),
            foto=EvidenceValue[str](raw="DSC001.JPG", normalized="DSC001.JPG", source_page=1),
            bbch=EvidenceValue[str](raw="65", normalized="65", source_page=1),
            observaciones=EvidenceValue[str](raw="Buen vigor", source_page=1),
            source_page=1,
        )
        self.assertEqual(row.id.normalized, "R001")
        self.assertEqual(row.especie.normalized, "P")
        self.assertEqual(row.altura_cm.normalized, 145.0)

    def test_estadillo_row_optional_fields_allow_all_none(self):
        row = EstadilloRow(source_page=1)
        self.assertIsNone(row.id)
        self.assertIsNone(row.especie)
        self.assertIsNone(row.col)
        self.assertIsNone(row.fil)
        self.assertEqual(row.source_page, 1)

    def test_estadillo_row_strict_col_type_rejection(self):
        with self.assertRaises(ValidationError):
            EstadilloRow(
                col=EvidenceValue[int](raw="1", normalized="1", source_page=1),  # type: ignore
                source_page=1,
            )

    def test_estadillo_row_evidence_source_page_mismatch_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            EstadilloRow(
                especie=EvidenceValue[str](raw="Quercus", source_page=2),  # Discrepa con source_page=1
                source_page=1,
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_estadillo_page_valid_consistency(self):
        header = EstadilloHeader(
            objetivo=EvidenceValue[str](raw="Inventario", source_page=1),
            source_page=1,
        )
        row1 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            especie=EvidenceValue[str](raw="Quercus", source_page=1),
            source_page=1,
        )
        page = EstadilloPage(
            page_number=1,
            header=header,
            rows=[row1],
            additional_text="Observaciones al pie de página",
        )
        self.assertEqual(page.page_number, 1)
        self.assertEqual(len(page.rows), 1)

    def test_estadillo_page_inconsistent_header_source_page_rejected(self):
        header = EstadilloHeader(
            source_page=2,  # Discrepa con page_number=1
        )
        with self.assertRaises(ValidationError) as ctx:
            EstadilloPage(
                page_number=1,
                header=header,
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_estadillo_page_inconsistent_row_source_page_rejected(self):
        row = EstadilloRow(
            source_page=2,  # Discrepa con page_number=1
        )
        with self.assertRaises(ValidationError) as ctx:
            EstadilloPage(
                page_number=1,
                rows=[row],
            )
        self.assertIn("source_page", str(ctx.exception))

    def test_estadillo_page_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            EstadilloPage(
                page_number=1,
                unknown_field="fail",  # type: ignore
            )

    def test_estadillo_document_unique_and_sorted_pages(self):
        page2 = EstadilloPage(page_number=2)
        page1 = EstadilloPage(page_number=1)
        doc = EstadilloDocument(
            source_file="26-05-06.pdf",
            pages=[page2, page1],
        )
        self.assertEqual([p.page_number for p in doc.pages], [1, 2])

    def test_estadillo_document_duplicate_page_rejected(self):
        page1_a = EstadilloPage(page_number=1)
        page1_b = EstadilloPage(page_number=1)
        with self.assertRaises(ValidationError) as ctx:
            EstadilloDocument(
                source_file="26-05-06.pdf",
                pages=[page1_a, page1_b],
            )
        self.assertIn("duplicado", str(ctx.exception).lower())

    def test_estadillo_document_roundtrip(self):
        doc = EstadilloDocument(
            source_file="26-05-06.pdf",
            pages=[
                EstadilloPage(
                    page_number=1,
                    header=EstadilloHeader(
                        objetivo=EvidenceValue[str](raw="Test", source_page=1),
                        source_page=1,
                    ),
                    rows=[
                        EstadilloRow(
                            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                            especie=EvidenceValue[str](raw="Pinus", source_page=1),
                            source_page=1,
                        )
                    ],
                )
            ],
        )
        dumped = doc.model_dump_json()
        loaded = EstadilloDocument.model_validate_json(dumped)
        self.assertEqual(doc, loaded)


if __name__ == "__main__":
    unittest.main()
