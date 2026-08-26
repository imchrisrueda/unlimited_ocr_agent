import unittest
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import EstadilloDocument, EstadilloPage, EstadilloRow
from src.fieldnotes.normalization.estadillo import normalize_species


class TestSpeciesNormalization(unittest.TestCase):
    def _create_doc_with_species(self, raw_species_list: list[str]) -> EstadilloDocument:
        rows = [
            EstadilloRow(
                id=EvidenceValue[str](raw=f"R{idx+1}", normalized=f"R{idx+1}", source_page=1),
                col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                fil=EvidenceValue[int](raw=str(idx+1), normalized=idx+1, source_page=1),
                especie=EvidenceValue[str](raw=raw_val, source_page=1),
                source_page=1,
            )
            for idx, raw_val in enumerate(raw_species_list)
        ]
        page = EstadilloPage(page_number=1, rows=rows)
        return EstadilloDocument(source_file="sample.pdf", pages=[page])

    def test_authorized_species_mappings(self):
        doc = self._create_doc_with_species([
            "Ap", "AP", "ap", "  Ap  ",
            "Ah", "AH", "ah",
            "Ar", "AR", "ar",
            "Mz", "MZ", "mz",
            "P", "H", "R", "M",
        ])

        norm_doc = normalize_species(doc)
        expected = [
            "P", "P", "P", "P",
            "H", "H", "H",
            "R", "R", "R",
            "M", "M", "M",
            "P", "H", "R", "M",
        ]
        rows = norm_doc.pages[0].rows
        for idx, exp in enumerate(expected):
            self.assertEqual(
                rows[idx].especie.normalized,
                exp,
                f"Fallo en fila {idx} con raw='{rows[idx].especie.raw}'",
            )
            # raw inalterado
            self.assertIsNotNone(rows[idx].especie.raw)

        # No debe haber advertencias para especies autorizadas
        self.assertEqual(len(norm_doc.warnings), 0)

    def test_m2_is_not_normalized_and_emits_unrecognized_species(self):
        doc = self._create_doc_with_species(["M2", "m2", " M2 "])
        norm_doc = normalize_species(doc)

        for row in norm_doc.pages[0].rows:
            self.assertIsNone(row.especie.normalized, "M2 no debe auto-corregirse a M")
            self.assertEqual(row.especie.raw.strip().upper(), "M2")

        # Debe generar 3 advertencias UNRECOGNIZED_SPECIES
        species_warnings = [w for w in norm_doc.warnings if w.code == "UNRECOGNIZED_SPECIES"]
        self.assertEqual(len(species_warnings), 3)

    def test_unrecognized_species_preserves_raw_and_warns(self):
        doc = self._create_doc_with_species(["Quercus robur", "Desconocido", "X"])
        norm_doc = normalize_species(doc)

        for row in norm_doc.pages[0].rows:
            self.assertIsNone(row.especie.normalized)
            self.assertIsNotNone(row.especie.raw)

        species_warnings = [w for w in norm_doc.warnings if w.code == "UNRECOGNIZED_SPECIES"]
        self.assertEqual(len(species_warnings), 3)

    def test_normalization_is_pure_and_does_not_mutate_input(self):
        doc = self._create_doc_with_species(["Ap", "M2"])
        self.assertIsNone(doc.pages[0].rows[0].especie.normalized)
        self.assertIsNone(doc.pages[0].rows[1].especie.normalized)
        self.assertEqual(len(doc.warnings), 0)

        norm_doc = normalize_species(doc)

        # El doc original permanece intacto
        self.assertIsNone(doc.pages[0].rows[0].especie.normalized)
        self.assertIsNone(doc.pages[0].rows[1].especie.normalized)
        self.assertEqual(len(doc.warnings), 0)

        # El nuevo doc contiene las normalizaciones
        self.assertEqual(norm_doc.pages[0].rows[0].especie.normalized, "P")
        self.assertIsNone(norm_doc.pages[0].rows[1].especie.normalized)
        self.assertEqual(len(norm_doc.warnings), 1)

    def test_normalization_idempotency_does_not_duplicate_warnings(self):
        doc = self._create_doc_with_species(["Ap", "M2"])
        pass1 = normalize_species(doc)
        pass2 = normalize_species(pass1)

        self.assertEqual(len(pass1.warnings), 1)
        self.assertEqual(len(pass2.warnings), 1)
        self.assertEqual(pass1.model_dump_json(), pass2.model_dump_json())


if __name__ == "__main__":
    unittest.main()
