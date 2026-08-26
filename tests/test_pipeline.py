import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import sys
from pathlib import Path
import shutil
from src.fieldnotes.artifacts import PageArtifact


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_pipeline_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_agent_initialization_worker_default(self, mock_lmstudio):
        mock_lmstudio.return_value = MagicMock()

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(model_name="test/model", output_dir=self.test_dir)

        self.assertEqual(agent.model_name, "test/model")
        self.assertEqual(agent.ocr_mode, "worker")
        self.assertIsNone(agent.ocr)
        mock_lmstudio.assert_called_once()

        # Accessing PyTorch properties in worker mode must raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            _ = agent.model
        self.assertIn("ocr_mode='worker'", str(ctx.exception))

        with self.assertRaises(RuntimeError):
            _ = agent.tokenizer

        with self.assertRaises(RuntimeError):
            _ = agent.device

        with self.assertRaises(RuntimeError):
            _ = agent.dtype

        agent.cleanup()

    @patch("src.fieldnotes.ocr.unlimited.UnlimitedOCR")
    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_agent_initialization_in_process(self, mock_lmstudio, mock_ocr):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance
        mock_vlm_instance = MagicMock()
        mock_lmstudio.return_value = mock_vlm_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(
            model_name="test/model", output_dir=self.test_dir, ocr_mode="in_process"
        )

        self.assertEqual(agent.ocr_mode, "in_process")
        self.assertIsNotNone(agent.ocr)

        # Delegated properties in in_process mode
        mock_ocr_instance.model = "ocr_model_1"
        self.assertEqual(agent.model, "ocr_model_1")
        agent.model = "ocr_model_2"
        self.assertEqual(mock_ocr_instance.model, "ocr_model_2")

        mock_ocr_instance.tokenizer = "tok_1"
        self.assertEqual(agent.tokenizer, "tok_1")
        agent.tokenizer = "tok_2"
        self.assertEqual(mock_ocr_instance.tokenizer, "tok_2")

        mock_ocr_instance.device = "cuda"
        self.assertEqual(agent.device, "cuda")
        agent.device = "cpu"
        self.assertEqual(mock_ocr_instance.device, "cpu")

        mock_ocr_instance.dtype = "bfloat16"
        self.assertEqual(agent.dtype, "bfloat16")
        agent.dtype = "float32"
        self.assertEqual(mock_ocr_instance.dtype, "float32")

        # LM studio properties work in both modes
        mock_vlm_instance.model = "qwen"
        self.assertEqual(agent.lm_model, "qwen")
        agent.lm_model = "qwen2"
        self.assertEqual(mock_vlm_instance.model, "qwen2")

        agent.cleanup()

    def test_invalid_ocr_mode_raises_value_error(self):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        with self.assertRaises(ValueError):
            UnlimitedOCRAgent(output_dir=self.test_dir, ocr_mode="invalid_mode")

    @patch("src.fieldnotes.pipeline.run_ocr_worker")
    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_extract_from_image_worker_mode(self, mock_lmstudio, mock_run_worker):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        mock_run_worker.return_value = (
            "image text from worker",
            [],
            {"status": "success", "mapping_status": "mapped"},
        )

        agent = UnlimitedOCRAgent(output_dir=self.test_dir, ocr_mode="worker")
        res = agent.extract_from_image("my_img.png")

        self.assertEqual(res, "image text from worker")
        mock_run_worker.assert_called_once_with(
            mode="image",
            model_name="baidu/Unlimited-OCR",
            image_paths=["my_img.png"],
            output_dir=agent.output_dir,
            timeout=None,
        )
        self.assertEqual(len(agent.page_artifacts), 1)
        self.assertEqual(agent.page_artifacts[0].raw_ocr, "image text from worker")
        self.assertEqual(agent.page_artifacts[0].ocr_mapping_status, "mapped")
        agent.cleanup()

    @patch("src.fieldnotes.pipeline.run_ocr_worker")
    @patch("src.fieldnotes.pipeline.extract_pdf_page_artifacts")
    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_extract_from_pdf_worker_mode(
        self, mock_lmstudio, mock_pdf_extract, mock_run_worker
    ):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(output_dir=self.test_dir, ocr_mode="worker")

        art1 = PageArtifact(page_number=1, image_path=Path(agent.output_dir) / "pages/page_001.png")
        mock_pdf_extract.return_value = [art1]

        # Simulate raw/page_001.md written by worker
        raw_dir = Path(agent.output_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "page_001.md").write_text("pagina 1 text", encoding="utf-8")

        mock_run_worker.return_value = (
            "<PAGE>pagina 1 text",
            [],
            {"status": "success", "mapping_status": "mapped", "mapping_error": None},
        )

        res = agent.extract_from_pdf("doc.pdf")
        self.assertEqual(res, "<PAGE>pagina 1 text")
        mock_pdf_extract.assert_called_once_with("doc.pdf", agent.output_dir)
        mock_run_worker.assert_called_once_with(
            mode="pdf",
            model_name="baidu/Unlimited-OCR",
            image_paths=[str(art1.image_path)],
            output_dir=agent.output_dir,
            timeout=None,
        )
        self.assertEqual(len(agent.page_artifacts), 1)
        self.assertEqual(agent.page_artifacts[0].raw_ocr, "pagina 1 text")
        self.assertEqual(agent.page_artifacts[0].ocr_mapping_status, "mapped")
        agent.cleanup()

    @patch("src.fieldnotes.pipeline.extract_pdf_page_artifacts")
    @patch("src.fieldnotes.ocr.unlimited.UnlimitedOCR")
    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_extract_from_pdf_in_process_mode(
        self, mock_lmstudio, mock_ocr, mock_pdf_extract
    ):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(output_dir=self.test_dir, ocr_mode="in_process")

        art1 = PageArtifact(page_number=1, image_path=Path(agent.output_dir) / "pages/page_001.png")
        mock_pdf_extract.return_value = [art1]
        mock_ocr_instance.extract_from_images.return_value = "<PAGE>in process text"
        mapped_art1 = PageArtifact(
            page_number=1,
            image_path=art1.image_path,
            raw_ocr="in process text",
            ocr_mapping_status="mapped",
        )
        mock_ocr_instance.process_page_artifacts.return_value = [mapped_art1]

        res = agent.extract_from_pdf("doc.pdf")
        self.assertEqual(res, "<PAGE>in process text")
        mock_ocr_instance.extract_from_images.assert_called_once_with([str(art1.image_path)])
        mock_ocr_instance.process_page_artifacts.assert_called_once_with([art1], "<PAGE>in process text")
        self.assertEqual(agent.page_artifacts, [mapped_art1])
        agent.cleanup()

    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_resolve_model_updates_attr(self, mock_lmstudio):
        mock_vlm_instance = MagicMock()
        mock_vlm_instance.resolve_model.return_value = "resolved-model"
        mock_lmstudio.return_value = mock_vlm_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(lm_model=None, output_dir=self.test_dir)

        res = agent._resolve_lm_model()
        self.assertEqual(res, "resolved-model")
        mock_vlm_instance.resolve_model.assert_called_once()
        agent.cleanup()

    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_ask_lmstudio_chunked(self, mock_lmstudio):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        agent.ask_lmstudio = MagicMock(
            side_effect=["partial1", "partial2", "final_synthesis"]
        )

        text = "a" * 1000
        res = agent.ask_lmstudio_chunked(
            text,
            "Summarize",
            chunk_size=600,
            chunk_overlap=100,
            reasoning_effort="none",
            max_tokens=100,
        )

        self.assertEqual(res, "final_synthesis")
        self.assertEqual(agent.ask_lmstudio.call_count, 3)
        agent.cleanup()

    @patch("fitz.open")
    def test_extract_pdf_page_artifacts_and_wrapper(self, mock_fitz_open):
        mock_doc = MagicMock()
        mock_page1 = MagicMock()
        mock_pix1 = MagicMock()
        mock_page1.get_pixmap.return_value = mock_pix1
        mock_page2 = MagicMock()
        mock_pix2 = MagicMock()
        mock_page2.get_pixmap.return_value = mock_pix2

        mock_doc.__iter__.return_value = [mock_page1, mock_page2]
        mock_fitz_open.return_value = mock_doc

        from src.fieldnotes.ingest.pdf import (
            extract_pdf_page_artifacts,
            extract_pdf_images,
        )

        artifacts = extract_pdf_page_artifacts("doc.pdf", self.test_dir)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(artifacts[0].page_number, 1)
        self.assertEqual(artifacts[0].image_path.name, "page_001.png")
        self.assertEqual(artifacts[1].page_number, 2)
        self.assertEqual(artifacts[1].image_path.name, "page_002.png")

        img_paths = extract_pdf_images("doc.pdf", self.test_dir)
        self.assertEqual(len(img_paths), 2)
        self.assertTrue(isinstance(img_paths[0], str))
        self.assertTrue(img_paths[0].endswith("page_001.png"))

    def test_torch_not_imported_in_parent_worker_mode(self):
        code = (
            "import sys\n"
            "from src.fieldnotes.pipeline import UnlimitedOCRAgent\n"
            "agent = UnlimitedOCRAgent(output_dir=r'" + self.test_dir + "', ocr_mode='worker')\n"
            "assert 'torch' not in sys.modules, 'torch was imported in parent'\n"
            "assert 'transformers' not in sys.modules, 'transformers was imported in parent'\n"
            "print('CLEAN_MODULES_OK')\n"
        )
        import subprocess
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Clean module check failed: {proc.stderr}")
        self.assertIn("CLEAN_MODULES_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
