import unittest
from pydantic import ValidationError
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import EstadilloRow, EstadilloPage, EstadilloDocument
from src.fieldnotes.validation.estadillo import validate_estadillo_document


class TestEstadilloValidators(unittest.TestCase):
    def _create_doc(self, rows: list[EstadilloRow]) -> EstadilloDocument:
        page = EstadilloPage(page_number=1, rows=rows)
        return EstadilloDocument(source_file="sample.pdf", pages=[page])

    def test_duplicate_coordinates_detection(self):
        r1 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="5", normalized=5, source_page=1),
            source_page=1,
        )
        r2 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="5", normalized=5, source_page=1),
            source_page=1,
        )
        r3 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="6", normalized=6, source_page=1),
            source_page=1,
        )
        doc = self._create_doc([r1, r2, r3])
        validated = validate_estadillo_document(doc)

        dup_warnings = [w for w in validated.warnings if w.code == "DUPLICATE_COORDINATES"]
        self.assertEqual(len(dup_warnings), 1)
        self.assertEqual(dup_warnings[0].details["col"], 1)
        self.assertEqual(dup_warnings[0].details["fil"], 5)

    def test_suspicious_row_sequence_discontinuity(self):
        # Secuencia con un salto de 22 a 2 y luego a 20
        fils = [26, 25, 24, 23, 22, 2, 20]
        rows = [
            EstadilloRow(
                col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                fil=EvidenceValue[int](raw=str(f), normalized=f, source_page=1),
                source_page=1,
            )
            for f in fils
        ]
        doc = self._create_doc(rows)
        validated = validate_estadillo_document(doc)

        seq_warnings = [w for w in validated.warnings if w.code == "SUSPICIOUS_ROW_SEQUENCE"]
        self.assertGreaterEqual(len(seq_warnings), 1)
        # El salto de 22 a 2 tiene delta=-20
        self.assertTrue(any(w.details.get("delta") == -20 for w in seq_warnings))

    def test_suspicious_row_sequence_direction_inversion(self):
        # Secuencia ascendente que invierte: 1, 2, 3, 2, 5
        fils = [1, 2, 3, 2, 5]
        rows = [
            EstadilloRow(
                col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                fil=EvidenceValue[int](raw=str(f), normalized=f, source_page=1),
                source_page=1,
            )
            for f in fils
        ]
        doc = self._create_doc(rows)
        validated = validate_estadillo_document(doc)

        seq_warnings = [w for w in validated.warnings if w.code == "SUSPICIOUS_ROW_SEQUENCE"]
        self.assertGreaterEqual(len(seq_warnings), 1)

    def test_invalid_bbch_format_and_unicode_digits(self):
        r_valid = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            bbch=EvidenceValue[str](raw="65", normalized="65", source_page=1),
            source_page=1,
        )
        r_invalid_long = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="2", normalized=2, source_page=1),
            bbch=EvidenceValue[str](raw="245", normalized="245", source_page=1),
            source_page=1,
        )
        r_invalid_single = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="3", normalized=3, source_page=1),
            bbch=EvidenceValue[str](raw="2", normalized="2", source_page=1),
            source_page=1,
        )
        # Dígitos de ancho completo (full-width) y arábigo-índicos no son ASCII [0-9]{2}
        r_fullwidth = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="4", normalized=4, source_page=1),
            bbch=EvidenceValue[str](raw="\uff16\uff15", normalized="\uff16\uff15", source_page=1),
            source_page=1,
        )
        r_arabic = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="5", normalized=5, source_page=1),
            bbch=EvidenceValue[str](raw="\u0666\u0665", normalized="\u0666\u0665", source_page=1),
            source_page=1,
        )

        doc = self._create_doc([r_valid, r_invalid_long, r_invalid_single, r_fullwidth, r_arabic])
        validated = validate_estadillo_document(doc)

        bbch_warnings = [w for w in validated.warnings if w.code == "INVALID_BBCH_FORMAT"]
        self.assertEqual(len(bbch_warnings), 4)

    def test_missing_coordinates(self):
        r_no_col = EstadilloRow(
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            source_page=1,
        )
        r_no_fil = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            source_page=1,
        )
        doc = self._create_doc([r_no_col, r_no_fil])
        validated = validate_estadillo_document(doc)

        coord_warnings = [w for w in validated.warnings if w.code == "MISSING_COORDINATES"]
        self.assertEqual(len(coord_warnings), 2)

    def test_altura_cm_finite_float_schema_boundary(self):
        # Valores no finitos en normalized son rechazados en el esquema
        with self.assertRaises(ValidationError):
            EstadilloRow(
                altura_cm=EvidenceValue[float](raw="NaN", normalized=float("nan"), source_page=1),
                source_page=1,
            )

        with self.assertRaises(ValidationError):
            EstadilloRow(
                altura_cm=EvidenceValue[float](raw="inf", normalized=float("inf"), source_page=1),
                source_page=1,
            )

        # Ambiguo o no numérico en raw con normalized=None es válido y preserva la evidencia
        row_raw_nan = EstadilloRow(
            altura_cm=EvidenceValue[float](raw="NaN", normalized=None, source_page=1),
            source_page=1,
        )
        doc = self._create_doc([row_raw_nan])
        json_str = doc.model_dump_json()
        loaded = EstadilloDocument.model_validate_json(json_str)
        self.assertEqual(loaded.pages[0].rows[0].altura_cm.raw, "NaN")
        self.assertIsNone(loaded.pages[0].rows[0].altura_cm.normalized)

    def test_suspicious_height_bounds(self):
        r_normal = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            altura_cm=EvidenceValue[float](raw="145", normalized=145.0, source_page=1),
            source_page=1,
        )
        r_negative = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="2", normalized=2, source_page=1),
            altura_cm=EvidenceValue[float](raw="-10", normalized=-10.0, source_page=1),
            source_page=1,
        )
        r_too_high = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="3", normalized=3, source_page=1),
            altura_cm=EvidenceValue[float](raw="850", normalized=850.0, source_page=1),
            source_page=1,
        )

        doc = self._create_doc([r_normal, r_negative, r_too_high])
        validated = validate_estadillo_document(doc, min_height_cm=0.0, max_height_cm=500.0)

        height_warnings = [w for w in validated.warnings if w.code == "SUSPICIOUS_HEIGHT"]
        self.assertEqual(len(height_warnings), 2)

    def test_validation_is_pure_and_idempotent(self):
        r1 = EstadilloRow(
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            bbch=EvidenceValue[str](raw="999", normalized="999", source_page=1),
            source_page=1,
        )
        doc = self._create_doc([r1])
        pass1 = validate_estadillo_document(doc)
        pass2 = validate_estadillo_document(pass1)

        self.assertEqual(len(pass1.warnings), 1)
        self.assertEqual(len(pass2.warnings), 1)
        self.assertEqual(pass1.model_dump_json(), pass2.model_dump_json())


if __name__ == "__main__":
    unittest.main()
