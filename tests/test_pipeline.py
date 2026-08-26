import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import shutil

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
        agent.cleanup()

    @patch('src.fieldnotes.pipeline.extract_pdf_images')
    @patch('src.fieldnotes.pipeline.UnlimitedOCR')
    @patch('src.fieldnotes.pipeline.LMStudioClient')
    def test_extract_pdf_delegation(self, mock_lmstudio, mock_ocr, mock_pdf_extract):
        mock_ocr_instance = MagicMock()
        mock_ocr.return_value = mock_ocr_instance
        mock_pdf_extract.return_value = ["page1.png"]
        mock_ocr_instance.extract_from_images.return_value = "pdf text"

        from src.fieldnotes.pipeline import UnlimitedOCRAgent
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        self.assertEqual(agent.extract_from_pdf("doc.pdf"), "pdf text")
        mock_pdf_extract.assert_called_once_with("doc.pdf", agent.output_dir)
        mock_ocr_instance.extract_from_images.assert_called_once_with(["page1.png"])
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
        res = agent.ask_lmstudio_chunked(text, "Summarize", chunk_size=600, chunk_overlap=100, reasoning_effort="none", max_tokens=100)

        self.assertEqual(res, "final_synthesis")
        self.assertEqual(agent.ask_lmstudio.call_count, 3)
        agent.cleanup()

if __name__ == '__main__':
    unittest.main()
