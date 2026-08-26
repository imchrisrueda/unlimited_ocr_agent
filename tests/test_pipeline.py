import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
from pathlib import Path
import shutil
from src.fieldnotes.artifacts import PageArtifact


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_agent_initialization(self, mock_lmstudio, mock_ocr):
        mock_ocr.return_value = MagicMock()
        mock_lmstudio.return_value = MagicMock()

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(model_name="test/model", output_dir=self.test_dir)

        self.assertEqual(agent.model_name, "test/model")
        mock_ocr.assert_called_once()
        mock_lmstudio.assert_called_once()
        agent.cleanup()

    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_live_delegated_properties(self, mock_lmstudio, mock_ocr):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance
        mock_vlm_instance = MagicMock()
        mock_lmstudio.return_value = mock_vlm_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        # Test reads
        mock_vlm_instance.model = "initial_lm"
        self.assertEqual(agent.lm_model, "initial_lm")

        # Test writes (live delegation)
        agent.lm_model = "override_lm"
        self.assertEqual(mock_vlm_instance.model, "override_lm")
        self.assertEqual(agent.lm_model, "override_lm")

        new_client = MagicMock()
        agent.llm_client = new_client
        self.assertEqual(mock_vlm_instance.client, new_client)
        self.assertEqual(agent.llm_client, new_client)

        agent.tokenizer = "new_tokenizer"
        self.assertEqual(mock_ocr_instance.tokenizer, "new_tokenizer")
        self.assertEqual(agent.tokenizer, "new_tokenizer")

        agent.device = "new_device"
        self.assertEqual(mock_ocr_instance.device, "new_device")
        self.assertEqual(agent.device, "new_device")

        agent.dtype = "new_dtype"
        self.assertEqual(mock_ocr_instance.dtype, "new_dtype")
        self.assertEqual(agent.dtype, "new_dtype")

        agent.model = "new_ocr_model"
        self.assertEqual(mock_ocr_instance.model, "new_ocr_model")
        self.assertEqual(agent.model, "new_ocr_model")

        agent.cleanup()

    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_extract_delegation(self, mock_lmstudio, mock_ocr):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        mock_ocr_instance.extract_from_image.return_value = "image text"
        self.assertEqual(agent.extract_from_image("img.png"), "image text")
        mock_ocr_instance.extract_from_image.assert_called_once_with("img.png")
        self.assertEqual(len(agent.page_artifacts), 1)
        self.assertEqual(agent.page_artifacts[0].raw_ocr, "image text")
        self.assertEqual(agent.page_artifacts[0].ocr_mapping_status, "mapped")
        agent.cleanup()

    @patch('src.fieldnotes.pipeline.extract_pdf_page_artifacts')
    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_extract_pdf_delegation(self, mock_lmstudio, mock_ocr, mock_pdf_extract):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance
        art1 = PageArtifact(page_number=1, image_path=Path(self.test_dir) / "pages/page_001.png")
        mock_pdf_extract.return_value = [art1]
        mock_ocr_instance.extract_from_images.return_value = "<PAGE>pdf text"
        mapped_art1 = PageArtifact(
            page_number=1,
            image_path=art1.image_path,
            raw_ocr="pdf text",
            ocr_mapping_status="mapped",
        )
        mock_ocr_instance.process_page_artifacts.return_value = [mapped_art1]

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        res = agent.extract_from_pdf("doc.pdf")
        self.assertEqual(res, "<PAGE>pdf text")
        mock_pdf_extract.assert_called_once_with("doc.pdf", agent.output_dir)
        mock_ocr_instance.extract_from_images.assert_called_once_with([str(art1.image_path)])
        mock_ocr_instance.process_page_artifacts.assert_called_once_with([art1], "<PAGE>pdf text")
        self.assertEqual(agent.page_artifacts, [mapped_art1])
        agent.cleanup()

    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_resolve_model_updates_attr(self, mock_lmstudio, mock_ocr):
        mock_vlm_instance = MagicMock()
        mock_vlm_instance.resolve_model.return_value = "resolved-model"
        mock_lmstudio.return_value = mock_vlm_instance

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(lm_model=None, output_dir=self.test_dir)

        res = agent._resolve_lm_model()
        self.assertEqual(res, "resolved-model")
        mock_vlm_instance.resolve_model.assert_called_once()
        agent.cleanup()

    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_ask_lmstudio_chunked(self, mock_lmstudio, mock_ocr):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        agent.ask_lmstudio = MagicMock(side_effect=["partial1", "partial2", "final_synthesis"])

        text = "a" * 1000
        res = agent.ask_lmstudio_chunked(
            text, "Summarize", chunk_size=600, chunk_overlap=100, reasoning_effort="none", max_tokens=100
        )

        self.assertEqual(res, "final_synthesis")
        self.assertEqual(agent.ask_lmstudio.call_count, 3)
        agent.cleanup()

    @patch('fitz.open')
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

        from src.fieldnotes.ingest.pdf import extract_pdf_page_artifacts, extract_pdf_images

        artifacts = extract_pdf_page_artifacts("doc.pdf", self.test_dir)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(artifacts[0].page_number, 1)
        self.assertEqual(artifacts[0].image_path.name, "page_001.png")
        self.assertEqual(artifacts[1].page_number, 2)
        self.assertEqual(artifacts[1].image_path.name, "page_002.png")

        # Probar wrapper extract_pdf_images
        img_paths = extract_pdf_images("doc.pdf", self.test_dir)
        self.assertEqual(len(img_paths), 2)
        self.assertTrue(isinstance(img_paths[0], str))
        self.assertTrue(img_paths[0].endswith("page_001.png"))


if __name__ == '__main__':
    unittest.main()
