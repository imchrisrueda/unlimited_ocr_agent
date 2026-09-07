import csv
import io
import unittest

from src.fieldnotes.render.estadillo_delivery import (
    CSV_COLUMNS,
    render_estadillo_csv,
    render_estadillo_notes,
    resolve_session_date,
)
from src.fieldnotes.schemas.estadillo import (
    EstadilloDocHeader,
    EstadilloDocument,
    EstadilloPage,
    EstadilloRow,
)
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.warnings import ExtractionWarning


def ev(raw, normalized, page=1):
    return EvidenceValue(raw=raw, normalized=normalized, source_page=page)


class TestEstadilloDelivery(unittest.TestCase):
    def make_document(self):
        header = EstadilloDocHeader(
            objetivo=ev("Toma de datos", "Toma de datos"),
            fecha=ev("2026-05-06", "2026-05-06"),
            asistentes=ev("CRA; NL; JMM", "CRA; NL; JMM"),
            especies_declaradas=ev(
                "Ap: Amaranthus Palmeri; Ah: Amaranthus Hybridus; Ar: Amaranthus Retroflexus; Mz: Maiz",
                None,
            ),
        )
        row = EstadilloRow(
            source_page=1,
            col=ev("1", 1),
            fil=ev("26", 26),
            especie=ev("Ah", "H"),
            altura_cm=ev("5.50", 5.5),
            observaciones=ev('Hojas, "comidas"\nen borde', 'Hojas, "comidas"\nen borde'),
        )
        page = EstadilloPage(page_number=1, rows=[row])
        return EstadilloDocument(source_file="source.pdf", header=header, pages=[page])

    def test_notes_front_matter_and_link_are_deterministic(self):
        doc = self.make_document()
        first = render_estadillo_notes(doc)
        second = render_estadillo_notes(doc)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('---\nobjetivo: "Toma de datos"\nfecha: 2026-05-06\n'))
        self.assertIn('  - "CRA"\n  - "NL"\n  - "JMM"', first)
        self.assertIn('  - "P": "Amaranthus Palmeri"', first)
        self.assertIn('datos: "datos.csv"\n---\n', first)
        self.assertNotIn("# Notas de campo", first)

    def test_csv_is_rectangular_and_rfc4180_parseable(self):
        text = render_estadillo_csv(self.make_document())
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[1]), len(CSV_COLUMNS))
        self.assertEqual(rows[1][1:5], ["1", "26", "H", "5.5"])
        self.assertEqual(rows[1][7], 'Hojas, "comidas"\nen borde')

    def test_session_date_requires_valid_non_conflicting_iso(self):
        doc = self.make_document()
        self.assertEqual(resolve_session_date(doc), "2026-05-06")

        spanish = doc.model_copy(deep=True)
        spanish.header.fecha = ev("06/05/26", "06/05/26")
        self.assertEqual(resolve_session_date(spanish), "2026-05-06")

        invalid = doc.model_copy(deep=True)
        invalid.header.fecha = ev("2026/05/06", "2026/05/06")
        self.assertIsNone(resolve_session_date(invalid))

        conflicting = doc.model_copy(deep=True)
        conflicting.warnings.append(
            ExtractionWarning(
                code="HEADER_CONFLICT",
                message="Fechas distintas",
                field_name="fecha",
            )
        )
        self.assertIsNone(resolve_session_date(conflicting))

    def test_additional_notes_preserve_page_provenance(self):
        doc = self.make_document()
        doc.additional_texts = [ev("Anotación observada", "Anotación observada", page=2)]
        text = render_estadillo_notes(doc)
        self.assertIn("# Notas de campo", text)
        self.assertIn("## Página 2", text)
        self.assertIn("Anotación observada", text)


if __name__ == "__main__":
    unittest.main()
