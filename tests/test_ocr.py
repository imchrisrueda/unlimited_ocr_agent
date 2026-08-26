import unittest
import tempfile
import os
import shutil
from pathlib import Path
from src.fieldnotes.artifacts import PageArtifact
from src.fieldnotes.ocr.unlimited import split_page_blocks, process_page_artifacts


class TestOCRSplitter(unittest.TestCase):
    def test_split_page_blocks_exact_match(self):
        raw_text = "<PAGE>Contenido página 1\n<PAGE>Contenido página 2\n<PAGE>Contenido página 3"
        status, blocks, error_msg = split_page_blocks(raw_text, 3)
        self.assertEqual(status, "mapped")
        self.assertIsNone(error_msg)
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0], "Contenido página 1\n")
        self.assertEqual(blocks[1], "Contenido página 2\n")
        self.assertEqual(blocks[2], "Contenido página 3")

    def test_split_page_blocks_preserves_verbatim_spaces_and_newlines(self):
        raw_text = "<PAGE>\n# Encabezado\n  - Elemento con sangría\n\n| a | b |\n|---|---|\n| 1 | 2 |\n<PAGE>Segunda página con  espacios dobles."
        status, blocks, error_msg = split_page_blocks(raw_text, 2)
        self.assertEqual(status, "mapped")
        self.assertIsNone(error_msg)
        self.assertEqual(len(blocks), 2)
        # Verifica fidelidad literal estricta:
        self.assertEqual(
            blocks[0],
            "\n# Encabezado\n  - Elemento con sangría\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        )
        self.assertEqual(blocks[1], "Segunda página con  espacios dobles.")

    def test_split_page_blocks_preface_detection(self):
        raw_text = "Texto prefacio no vacío antes del primer tag<PAGE>Página 1<PAGE>Página 2"
        status, blocks, error_msg = split_page_blocks(raw_text, 2)
        self.assertEqual(status, "unaligned")
        self.assertIsNone(blocks)
        self.assertIn("texto no vacío previo", error_msg)

    def test_split_page_blocks_count_mismatch_fewer(self):
        raw_text = "<PAGE>Página 1<PAGE>Página 2"
        status, blocks, error_msg = split_page_blocks(raw_text, 3)
        self.assertEqual(status, "unaligned")
        self.assertIsNone(blocks)
        self.assertIn("Discrepancia en el conteo de páginas", error_msg)
        self.assertIn("se esperaban 3", error_msg)

    def test_split_page_blocks_count_mismatch_more(self):
        raw_text = "<PAGE>Página 1<PAGE>Página 2<PAGE>Página 3"
        status, blocks, error_msg = split_page_blocks(raw_text, 2)
        self.assertEqual(status, "unaligned")
        self.assertIsNone(blocks)
        self.assertIn("Discrepancia en el conteo de páginas", error_msg)
        self.assertIn("se esperaban 2", error_msg)

    def test_split_page_blocks_no_delimiters(self):
        raw_text = "Documento sin marcas de página."
        status, blocks, error_msg = split_page_blocks(raw_text, 2)
        self.assertEqual(status, "unaligned")
        self.assertIsNone(blocks)
        self.assertIn("No se encontraron delimitadores", error_msg)

    def test_split_page_blocks_empty_text(self):
        status, blocks, error_msg = split_page_blocks("", 2)
        self.assertEqual(status, "unaligned")
        self.assertIsNone(blocks)
        self.assertIn("está vacío", error_msg)


class TestProcessPageArtifacts(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_process_page_artifacts_mapped(self):
        artifacts = [
            PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png"),
            PageArtifact(page_number=2, image_path=Path(self.test_dir) / "pages/page_002.png"),
        ]
        raw_text = "<PAGE>Texto página 1\n<PAGE>Texto página 2"
        res = process_page_artifacts(artifacts, raw_text, self.test_dir)

        # raw/document.md debe ser byte y textualmente idéntico
        doc_path = os.path.join(self.test_dir, "raw", "document.md")
        self.assertTrue(os.path.exists(doc_path))
        with open(doc_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), raw_text)

        # Archivos por página
        p1_path = os.path.join(self.test_dir, "raw", "page_001.md")
        p2_path = os.path.join(self.test_dir, "raw", "page_002.md")
        self.assertTrue(os.path.exists(p1_path))
        self.assertTrue(os.path.exists(p2_path))
        with open(p1_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Texto página 1\n")
        with open(p2_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Texto página 2")

        self.assertEqual(res[0].ocr_mapping_status, "mapped")
        self.assertEqual(res[0].raw_ocr, "Texto página 1\n")
        self.assertIsNone(res[0].mapping_error)
        self.assertEqual(res[1].ocr_mapping_status, "mapped")
        self.assertEqual(res[1].raw_ocr, "Texto página 2")

    def test_process_page_artifacts_unaligned(self):
        artifacts = [
            PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png"),
            PageArtifact(page_number=2, image_path=Path(self.test_dir) / "pages/page_002.png"),
        ]
        raw_text = "<PAGE>Solo una página"
        res = process_page_artifacts(artifacts, raw_text, self.test_dir)

        # raw/document.md se conserva siempre
        doc_path = os.path.join(self.test_dir, "raw", "document.md")
        self.assertTrue(os.path.exists(doc_path))
        with open(doc_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), raw_text)

        # NO deben crearse archivos por página
        p1_path = os.path.join(self.test_dir, "raw", "page_001.md")
        p2_path = os.path.join(self.test_dir, "raw", "page_002.md")
        self.assertFalse(os.path.exists(p1_path))
        self.assertFalse(os.path.exists(p2_path))

        self.assertEqual(res[0].ocr_mapping_status, "unaligned")
        self.assertIsNone(res[0].raw_ocr)
        self.assertIsNotNone(res[0].mapping_error)
        self.assertEqual(res[1].ocr_mapping_status, "unaligned")
        self.assertIsNone(res[1].raw_ocr)

    def test_mapped_followed_by_unaligned_cleans_obsolete_pages(self):
        artifacts = [
            PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png"),
            PageArtifact(page_number=2, image_path=Path(self.test_dir) / "pages/page_002.png"),
        ]
        raw_text_mapped = "<PAGE>Pagina 1\n<PAGE>Pagina 2"
        process_page_artifacts(artifacts, raw_text_mapped, self.test_dir)

        p1_path = os.path.join(self.test_dir, "raw", "page_001.md")
        p2_path = os.path.join(self.test_dir, "raw", "page_002.md")
        self.assertTrue(os.path.exists(p1_path))
        self.assertTrue(os.path.exists(p2_path))

        # Segunda llamada con resultado unaligned
        raw_text_unaligned = "<PAGE>Solo un bloque desalineado"
        res = process_page_artifacts(artifacts, raw_text_unaligned, self.test_dir)

        # Los archivos page_*.md deben haber sido eliminados con seguridad
        self.assertFalse(os.path.exists(p1_path))
        self.assertFalse(os.path.exists(p2_path))
        self.assertEqual(res[0].ocr_mapping_status, "unaligned")
        self.assertEqual(res[1].ocr_mapping_status, "unaligned")

        # raw/document.md debe contener el texto del unaligned
        doc_path = os.path.join(self.test_dir, "raw", "document.md")
        self.assertTrue(os.path.exists(doc_path))
        with open(doc_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), raw_text_unaligned)

    def test_binary_exact_lf_preservation_from_result_file(self):
        # Crear un result.md con terminaciones LF explícitas en binario
        result_content_bytes = b"# Titulo\n\n<PAGE>Pagina 1 con LF\nlinea 2\n<PAGE>Pagina 2 con LF\nlinea 2\n"
        result_file = os.path.join(self.test_dir, "result.md")
        with open(result_file, "wb") as f:
            f.write(result_content_bytes)

        artifacts = [
            PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png"),
            PageArtifact(page_number=2, image_path=Path(self.test_dir) / "pages/page_002.png"),
        ]
        raw_text = result_content_bytes.decode("utf-8")
        process_page_artifacts(artifacts, raw_text, self.test_dir, source_result_path=result_file)

        doc_path = os.path.join(self.test_dir, "raw", "document.md")
        self.assertTrue(os.path.exists(doc_path))
        with open(doc_path, "rb") as f:
            doc_bytes = f.read()

        # Comprobar identidad binaria estricta (sin conversión CRLF en Windows)
        self.assertEqual(doc_bytes, result_content_bytes)
        self.assertNotIn(b"\r\n", doc_bytes)

    def test_foreign_files_in_raw_are_preserved(self):
        raw_dir = os.path.join(self.test_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        foreign_file_1 = os.path.join(raw_dir, "custom_notes.txt")
        foreign_file_2 = os.path.join(raw_dir, "extra_data.json")

        with open(foreign_file_1, "w", encoding="utf-8") as f:
            f.write("notas importantes ajenas")
        with open(foreign_file_2, "w", encoding="utf-8") as f:
            f.write('{"key": "value"}')

        artifacts = [
            PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png"),
            PageArtifact(page_number=2, image_path=Path(self.test_dir) / "pages/page_002.png"),
        ]
        raw_text = "<PAGE>Pagina 1\n<PAGE>Pagina 2"
        process_page_artifacts(artifacts, raw_text, self.test_dir)

        # Verificar que los archivos ajenos siguen existiendo intactos
        self.assertTrue(os.path.exists(foreign_file_1))
        self.assertTrue(os.path.exists(foreign_file_2))
        with open(foreign_file_1, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "notas importantes ajenas")
        with open(foreign_file_2, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"key": "value"}')


class TestRealOCRIntegration(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RUN_OCR_INTEGRATION") == "1",
        "Prueba de integración real desactivada por defecto; requiere RUN_OCR_INTEGRATION=1",
    )
    def test_real_ocr_pipeline_on_pdf(self):
        sample_pdf = "26-05-06.pdf"
        if not os.path.exists(sample_pdf):
            self.skipTest(f"Archivo {sample_pdf} no encontrado.")

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        test_dir = tempfile.mkdtemp()
        try:
            agent = UnlimitedOCRAgent(output_dir=test_dir)
            text = agent.extract_from_pdf(sample_pdf)
            self.assertTrue(len(text) > 0)
            self.assertTrue(len(agent.page_artifacts) > 0)
            doc_path = os.path.join(agent.output_dir, "raw", "document.md")
            self.assertTrue(os.path.exists(doc_path))
            agent.cleanup()
        finally:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)


if __name__ == "__main__":
    unittest.main()
