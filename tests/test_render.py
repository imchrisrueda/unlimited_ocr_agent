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
from src.fieldnotes.render.markdown import render_estadillo_markdown, escape_markdown_cell, format_evidence_cell


class TestEstadilloMarkdownRender(unittest.TestCase):
    def test_cell_escaping(self):
        self.assertEqual(escape_markdown_cell("texto simple"), "texto simple")
        self.assertEqual(escape_markdown_cell("texto | con | pipes"), r"texto \| con \| pipes")
        self.assertEqual(
            escape_markdown_cell("linea 1\nlinea 2\r\nlinea 3"),
            "linea 1 linea 2 linea 3",
        )
        self.assertEqual(escape_markdown_cell(None), "")

    def test_format_evidence_cell_priority_and_numbers(self):
        # Normalized tiene prioridad
        ev_norm = EvidenceValue[str](raw="p", normalized="P", source_page=1)
        self.assertEqual(format_evidence_cell(ev_norm), "P")

        # Raw se usa cuando normalized es None
        ev_raw = EvidenceValue[str](raw="Desconocido", source_page=1)
        self.assertEqual(format_evidence_cell(ev_raw), "Desconocido")

        # Flotante con valor entero se formatea como entero
        ev_float_int = EvidenceValue[float](raw="145.0", normalized=145.0, source_page=1)
        self.assertEqual(format_evidence_cell(ev_float_int), "145")

        ev_float_zero = EvidenceValue[float](raw="0.0", normalized=0.0, source_page=1)
        self.assertEqual(format_evidence_cell(ev_float_zero), "0")

        # Flotante decimal se formatea limpio
        ev_float_dec = EvidenceValue[float](raw="145.5", normalized=145.5, source_page=1)
        self.assertEqual(format_evidence_cell(ev_float_dec), "145.5")

        # None
        self.assertEqual(format_evidence_cell(None), "")

    def test_format_evidence_cell_no_arbitrary_precision_loss_and_exponents(self):
        """Regresión: valores con más de 6 decimales y exponentes no se truncan ni emiten notación científica."""
        # >6 decimales
        ev_7_dec = EvidenceValue[float](raw="145.1234567", normalized=145.1234567, source_page=1)
        self.assertEqual(format_evidence_cell(ev_7_dec), "145.1234567")

        ev_9_dec = EvidenceValue[float](raw="145.123456789", normalized=145.123456789, source_page=1)
        self.assertEqual(format_evidence_cell(ev_9_dec), "145.123456789")

        # Exponentes pequeños (que en str(float) generan 1e-07 o 1.5e-05)
        ev_exp_small = EvidenceValue[float](raw="1e-7", normalized=1e-7, source_page=1)
        self.assertEqual(format_evidence_cell(ev_exp_small), "0.0000001")
        self.assertNotIn("e", format_evidence_cell(ev_exp_small).lower())

        ev_exp_small_frac = EvidenceValue[float](raw="1.5e-5", normalized=1.5e-5, source_page=1)
        self.assertEqual(format_evidence_cell(ev_exp_small_frac), "0.000015")
        self.assertNotIn("e", format_evidence_cell(ev_exp_small_frac).lower())

        ev_exp_multidigit = EvidenceValue[float](raw="1.23456e-5", normalized=1.23456e-5, source_page=1)
        self.assertEqual(format_evidence_cell(ev_exp_multidigit), "0.0000123456")

        # Exponentes grandes enteros
        ev_exp_large = EvidenceValue[float](raw="1e8", normalized=1e8, source_page=1)
        self.assertEqual(format_evidence_cell(ev_exp_large), "100000000")

        # Números negativos
        ev_neg_dec = EvidenceValue[float](raw="-145.123456789", normalized=-145.123456789, source_page=1)
        self.assertEqual(format_evidence_cell(ev_neg_dec), "-145.123456789")

        ev_neg_exp = EvidenceValue[float](raw="-5e-5", normalized=-5e-5, source_page=1)
        self.assertEqual(format_evidence_cell(ev_neg_exp), "-0.00005")

        # Sin comas locales
        for ev in [ev_7_dec, ev_9_dec, ev_exp_small, ev_exp_small_frac, ev_neg_dec]:
            res = format_evidence_cell(ev)
            self.assertNotIn(",", res)

    def test_render_exact_two_tables_agents_md(self):
        page_header = EstadilloPageHeader(
            objetivo=EvidenceValue[str](raw="Parcela A", source_page=1),
            fecha=EvidenceValue[str](raw="2026-05-06", source_page=1),
            asistentes=EvidenceValue[str](raw="C. Rueda | M. Perez", source_page=1),
            equipamiento=EvidenceValue[str](raw="GPS, cinta", source_page=1),
            situacion_atmosferica=EvidenceValue[str](raw="Soleado", source_page=1),
            source_page=1,
        )
        row1 = EstadilloRow(
            id=EvidenceValue[str](raw="R1", normalized="R1", source_page=1),
            col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
            especie=EvidenceValue[str](raw="Ap", normalized="P", source_page=1),
            altura_cm=EvidenceValue[float](raw="145", normalized=145.0, source_page=1),
            foto=EvidenceValue[str](raw="DSC01.JPG", normalized="DSC01.JPG", source_page=1),
            bbch=EvidenceValue[str](raw="65", normalized="65", source_page=1),
            observaciones=EvidenceValue[str](raw="Vigor | alto\nsegunda linea", source_page=1),
            source_page=1,
        )
        page1 = EstadilloPage(page_number=1, header=page_header, rows=[row1])
        doc_header = EstadilloDocHeader(
            objetivo=EvidenceValue[str](raw="Parcela A", source_page=1),
            fecha=EvidenceValue[str](raw="2026-05-06", source_page=1),
            asistentes=EvidenceValue[str](raw="C. Rueda | M. Perez", source_page=1),
            equipamiento=EvidenceValue[str](raw="GPS, cinta", source_page=1),
            situacion_atmosferica=EvidenceValue[str](raw="Soleado", source_page=1),
            source_page=None,
        )
        doc = EstadilloDocument(
            source_file="doc.pdf",
            pages=[page1],
            header=doc_header,
            additional_texts=[
                EvidenceValue[str](raw="Anotacion marginal", source_page=1)
            ],
        )

        md = render_estadillo_markdown(doc)
        lines = md.splitlines()

        # Comprobar inicio con primera tabla obligatoria (3x2)
        self.assertEqual(lines[0], "| Objetivo | Fecha | Asistentes |")
        self.assertEqual(lines[1], "|---|---|---|")
        self.assertEqual(lines[2], r"| Parcela A | 2026-05-06 | C. Rueda \| M. Perez |")
        self.assertEqual(lines[3], "| Equipamiento: GPS, cinta | Situación atmosférica: Soleado | Especies: P;H;R;M |")
        self.assertEqual(lines[4], "")

        # Comprobar segunda tabla obligatoria (8 columnas)
        self.assertEqual(lines[5], "|id|col|fil|especie|altura_cm|foto|bbch|observaciones|")
        self.assertEqual(lines[6], "|---|---|---|---|---|---|---|---|")
        self.assertEqual(lines[7], r"|R1|1|1|P|145|DSC01.JPG|65|Vigor \| alto segunda linea|")

        # Comprobar información adicional
        self.assertIn("## Información adicional", md)
        self.assertIn("### Página 1", md)
        self.assertIn("Anotacion marginal", md)

    def test_render_empty_fields_leave_cells_blank(self):
        doc = EstadilloDocument(
            source_file="empty.pdf",
            pages=[
                EstadilloPage(
                    page_number=1,
                    rows=[EstadilloRow(source_page=1)],  # Fila vacía
                )
            ],
        )
        md = render_estadillo_markdown(doc)
        lines = md.splitlines()

        # Primera tabla vacía
        self.assertEqual(lines[2], "|  |  |  |")
        self.assertEqual(lines[3], "| Equipamiento:  | Situación atmosférica:  | Especies: P;H;R;M |")

        # Segunda tabla vacía
        self.assertEqual(lines[7], "|||||||||")


if __name__ == "__main__":
    unittest.main()
