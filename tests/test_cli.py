import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from src.fieldnotes.cli import build_parser, main
from src.fieldnotes.artifacts import PageArtifact


class TestCLI(unittest.TestCase):
    def test_build_parser(self):
        parser = build_parser()
        args = parser.parse_args(["test.pdf", "--raw"])
        self.assertEqual(args.file_path, "test.pdf")
        self.assertTrue(args.raw)
        self.assertFalse(args.keep_intermediate)
        self.assertEqual(args.chunk_size, 0)
        self.assertEqual(args.chunk_overlap, 500)
        self.assertEqual(args.max_tokens, 2048)
        self.assertEqual(args.reasoning_effort, "none")
        self.assertEqual(args.prompt, "Digitaliza este documento manteniendo su estructura en Markdown limpio.")

    def test_parser_vision_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "doc.pdf",
            "--vision-model", "qwen/qwen3.5-9b",
            "--text-model", "qwen-text",
            "--ask-vision",
        ])
        self.assertEqual(args.vision_model, "qwen/qwen3.5-9b")
        self.assertEqual(args.text_model, "qwen-text")
        self.assertTrue(args.ask_vision)

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_raw(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_image.return_value = "raw ocr output"

        main(["image.png", "--raw"])

        mock_agent_class.assert_called_once()
        mock_agent.extract_from_image.assert_called_once_with("image.png")
        mock_agent.ask_lmstudio.assert_not_called()

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_pdf(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_pdf.return_value = "pdf ocr output"

        main(["doc.pdf", "--raw"])
        mock_agent.extract_from_pdf.assert_called_once_with("doc.pdf")

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_ask_vision_single_image(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_image.return_value = "ocr text image"
        mock_agent.ask_vision.return_value = "vision answer"

        main(["image.png", "--ask-vision", "--vision-model", "qwen/qwen3.5-9b"])

        mock_agent.extract_from_image.assert_called_once_with("image.png")
        mock_agent.ask_vision.assert_called_once()
        kwargs = mock_agent.ask_vision.call_args.kwargs
        self.assertEqual(kwargs["image_path"], "image.png")
        self.assertEqual(kwargs["ocr_context"], "ocr text image")

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_ask_vision_pdf_sequential(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        art1 = PageArtifact(page_number=1, image_path=Path("page_001.png"), raw_ocr="p1")
        art2 = PageArtifact(page_number=2, image_path=Path("page_002.png"), raw_ocr="p2")
        mock_agent.extract_from_pdf.return_value = "doc text"
        mock_agent.page_artifacts = [art1, art2]
        mock_agent.ask_page_vision.side_effect = ["resp page 1", "resp page 2"]

        main(["doc.pdf", "--ask-vision", "--vision-model", "qwen/qwen3.5-9b"])

        mock_agent.extract_from_pdf.assert_called_once_with("doc.pdf")
        self.assertEqual(mock_agent.ask_page_vision.call_count, 2)
        mock_agent.ask_page_vision.assert_any_call(
            art1,
            prompt="Digitaliza este documento manteniendo su estructura en Markdown limpio.",
            reasoning_effort="none",
            max_tokens=2048,
        )
        mock_agent.ask_page_vision.assert_any_call(
            art2,
            prompt="Digitaliza este documento manteniendo su estructura en Markdown limpio.",
            reasoning_effort="none",
            max_tokens=2048,
        )

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_ask_vision_with_only_legacy_env(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_image.return_value = "ocr text image"
        mock_agent.ask_vision.return_value = "vision answer"

        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "legacy_qwen_model"}, clear=True):
            main(["image.png", "--ask-vision"])

        mock_agent_class.assert_called_once()
        kwargs = mock_agent_class.call_args.kwargs
        self.assertEqual(kwargs["vision_model"], "legacy_qwen_model")
        self.assertEqual(kwargs["text_model"], "legacy_qwen_model")

    @patch("builtins.print")
    def test_main_execution_ask_vision_without_model_exits_with_error(self, mock_print):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                main(["image.png", "--ask-vision"])
            self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
