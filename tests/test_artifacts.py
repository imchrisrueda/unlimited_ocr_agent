import unittest
from pathlib import Path
from src.fieldnotes.artifacts import PageArtifact


class TestPageArtifact(unittest.TestCase):
    def test_default_values(self):
        artifact = PageArtifact(page_number=1, image_path="pages/page_001.png")
        self.assertEqual(artifact.page_number, 1)
        self.assertEqual(artifact.image_path, Path("pages/page_001.png"))
        self.assertIsNone(artifact.raw_ocr)
        self.assertEqual(artifact.ocr_mapping_status, "pending")
        self.assertIsNone(artifact.mapping_error)

    def test_explicit_initialization(self):
        img_path = Path("/tmp/run/pages/page_002.png")
        artifact = PageArtifact(
            page_number=2,
            image_path=img_path,
            raw_ocr="Texto de página 2",
            ocr_mapping_status="mapped",
            mapping_error=None,
        )
        self.assertEqual(artifact.page_number, 2)
        self.assertEqual(artifact.image_path, img_path)
        self.assertEqual(artifact.raw_ocr, "Texto de página 2")
        self.assertEqual(artifact.ocr_mapping_status, "mapped")
        self.assertIsNone(artifact.mapping_error)

    def test_unaligned_status(self):
        artifact = PageArtifact(
            page_number=3,
            image_path="page_003.png",
            raw_ocr=None,
            ocr_mapping_status="unaligned",
            mapping_error="Discrepancia en conteo",
        )
        self.assertEqual(artifact.ocr_mapping_status, "unaligned")
        self.assertEqual(artifact.mapping_error, "Discrepancia en conteo")
        self.assertIsNone(artifact.raw_ocr)


if __name__ == "__main__":
    unittest.main()
