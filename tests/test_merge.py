import unittest
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import (
    EstadilloPageHeader,
    EstadilloDocHeader,
    EstadilloHeader,
    EstadilloRow,
    EstadilloPage,
    EstadilloDocument,
)
from src.fieldnotes.merge.estadillo import merge_estadillo_pages


class TestEstadilloMerge(unittest.TestCase):
    def test_merge_empty_pages(self):
        doc = merge_estadillo_pages([], source_file="empty.pdf")
        self.assertEqual(len(doc.pages), 0)
        self.assertIsNone(doc.header)
        self.assertEqual(len(doc.warnings), 0)

    def test_merge_multipage_deterministic_sorting_and_rows_order(self):
        r1_p2 = EstadilloRow(id=EvidenceValue[str](raw="R2", source_page=2), source_page=2)
        p2 = EstadilloPage(page_number=2, rows=[r1_p2], additional_text="Nota pagina 2")

        r1_p1 = EstadilloRow(id=EvidenceValue[str](raw="R1", source_page=1), source_page=1)
        p1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(objetivo=EvidenceValue[str](raw="Muestreo", source_page=1), source_page=1),
            rows=[r1_p1],
            additional_text="Nota pagina 1",
        )

        # Entrada desordenada
        doc = merge_estadillo_pages([p2, p1], source_file="doc.pdf")

        # Páginas ordenadas
        self.assertEqual([p.page_number for p in doc.pages], [1, 2])
        self.assertEqual(doc.pages[0].rows[0].id.raw, "R1")
        self.assertEqual(doc.pages[1].rows[0].id.raw, "R2")

        # Additional texts
        self.assertEqual(len(doc.additional_texts), 2)
        self.assertEqual(doc.additional_texts[0].source_page, 1)
        self.assertEqual(doc.additional_texts[0].raw, "Nota pagina 1")
        self.assertEqual(doc.additional_texts[1].source_page, 2)
        self.assertEqual(doc.additional_texts[1].raw, "Nota pagina 2")

    def test_header_provenance_preserved_across_different_pages(self):
        p1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="Inventario Forestal", source_page=1),
                source_page=1,
            ),
        )
        p2 = EstadilloPage(
            page_number=2,
            header=EstadilloPageHeader(
                asistentes=EvidenceValue[str](raw="Tecnico A, Tecnico B", source_page=2),
                source_page=2,
            ),
        )
        p3 = EstadilloPage(
            page_number=3,
            header=EstadilloPageHeader(
                fecha=EvidenceValue[str](raw="2026-05-06", source_page=3),
                source_page=3,
            ),
        )

        doc = merge_estadillo_pages([p1, p2, p3], source_file="doc.pdf")
        self.assertIsNotNone(doc.header)
        self.assertIsNone(doc.header.source_page, "Cabecera agregada debe tener source_page=None")
        self.assertEqual(doc.header.objetivo.source_page, 1)
        self.assertEqual(doc.header.asistentes.source_page, 2)
        self.assertEqual(doc.header.fecha.source_page, 3)

        # Verificar roundtrip de serialización
        json_data = doc.model_dump_json()
        loaded = EstadilloDocument.model_validate_json(json_data)
        self.assertIsNone(loaded.header.source_page)
        self.assertEqual(loaded.header.objetivo.source_page, 1)
        self.assertEqual(loaded.header.asistentes.source_page, 2)
        self.assertEqual(loaded.header.fecha.source_page, 3)

    def test_header_whitespace_collapse_equivalence_and_newline_handling(self):
        p1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="Inventario\n\nRodal   A", source_page=1),
                source_page=1,
            ),
        )
        p2 = EstadilloPage(
            page_number=2,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="  Inventario Rodal A  ", source_page=2),
                source_page=2,
            ),
        )

        doc = merge_estadillo_pages([p1, p2], source_file="doc.pdf")
        self.assertIsNotNone(doc.header)
        # Se retiene el raw exacto de la primera página
        self.assertEqual(doc.header.objetivo.raw, "Inventario\n\nRodal   A")
        # No se emite conflicto porque el colapso de espacios es idéntico
        self.assertEqual(len(doc.warnings), 0)

    def test_header_reconciliation_conflict_retains_first_and_emits_warning(self):
        p1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="Inventario Rodal A", source_page=1),
                source_page=1,
            ),
        )
        p2 = EstadilloPage(
            page_number=2,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="Inventario Rodal B", source_page=2),
                source_page=2,
            ),
        )

        doc = merge_estadillo_pages([p1, p2], source_file="doc.pdf")
        self.assertIsNotNone(doc.header)
        self.assertEqual(doc.header.objetivo.raw, "Inventario Rodal A")

        conflict_warnings = [w for w in doc.warnings if w.code == "HEADER_CONFLICT"]
        self.assertEqual(len(conflict_warnings), 1)
        self.assertEqual(conflict_warnings[0].field_name, "objetivo")
        self.assertEqual(conflict_warnings[0].details["winner_page"], 1)
        self.assertEqual(conflict_warnings[0].details["conflicts"][0]["page"], 2)

    def test_merge_is_pure_and_idempotent(self):
        p1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(objetivo=EvidenceValue[str](raw="Test", source_page=1), source_page=1),
            rows=[EstadilloRow(id=EvidenceValue[str](raw="R1", source_page=1), source_page=1)],
        )
        pages = [p1]

        doc1 = merge_estadillo_pages(pages, "doc.pdf")
        doc2 = merge_estadillo_pages(pages, "doc.pdf")

        self.assertEqual(doc1.model_dump_json(), doc2.model_dump_json())
        # Input no mutado
        self.assertEqual(len(pages[0].rows), 1)


if __name__ == "__main__":
    unittest.main()
