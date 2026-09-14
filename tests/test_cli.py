import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from src.fieldnotes.cli import build_parser, main
from src.fieldnotes.artifacts import PageArtifact


class TestCLI(unittest.TestCase):
    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    def test_cleanup_on_ocr_failure(self, mock_agent_class):
        agent = mock_agent_class.return_value
        agent.extract_from_pdf.side_effect = RuntimeError("OCR failed")
        with self.assertRaisesRegex(RuntimeError, "OCR failed"):
            main(["doc.pdf", "--raw", "--no-progress"])
        agent.unload_all.assert_called_once()
        agent.cleanup.assert_called_once()

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    def test_keep_models_on_failure_still_releases_ocr(self, mock_agent_class):
        agent = mock_agent_class.return_value
        agent.extract_from_pdf.side_effect = RuntimeError("OCR failed")
        with self.assertRaises(RuntimeError):
            main(["doc.pdf", "--raw", "--keep-models-loaded", "--keep-intermediate"])
        agent.unload_ocr.assert_called_once()
        agent.unload_all.assert_not_called()
        agent.cleanup.assert_not_called()
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

    def test_main_execution_missing_file_path_and_images_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_parser_usability_flags(self):
        parser = build_parser()
        args = parser.parse_args(["doc.pdf", "--no-progress", "--quiet", "--verbose"])
        self.assertTrue(args.no_progress)
        self.assertTrue(args.quiet)
        self.assertTrue(args.verbose)

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_folder_with_images_cuaderno_campo(self, mock_print, mock_agent_class):
        import tempfile
        import shutil
        from PIL import Image

        temp_dir = Path(tempfile.mkdtemp(prefix="test_cli_folder_"))
        try:
            folder = temp_dir / "2026-09-14"
            folder.mkdir()
            img1 = folder / "p1.png"
            Image.new("RGB", (20, 20), "red").save(img1)
            img2 = folder / "p2.png"
            Image.new("RGB", (20, 20), "blue").save(img2)

            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent
            mock_doc = MagicMock()
            mock_doc.pages = []
            mock_doc.estadillo_rows = []
            mock_doc.sections = []
            mock_doc.tables = []
            mock_doc.diagrams = []
            mock_doc.warnings = []
            mock_agent.process_cuaderno_campo.return_value = ("# Markdown", mock_doc)

            main([
                str(folder),
                "--profile", "cuaderno_campo",
                "--vision-model", "qwen/qwen3.5-9b",
                "--no-progress",
            ])

            mock_agent.process_cuaderno_campo.assert_called_once()
            called_file_path = mock_agent.process_cuaderno_campo.call_args.kwargs["file_path"]
            # Debe haberse convertido a un PDF
            self.assertTrue(called_file_path.lower().endswith(".pdf"))
            self.assertIn("2026-09-14", Path(called_file_path).stem)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("src.fieldnotes.cli.UnlimitedOCRAgent")
    @patch("builtins.print")
    def test_main_execution_images_flag(self, mock_print, mock_agent_class):
        import tempfile
        import shutil
        from PIL import Image

        temp_dir = Path(tempfile.mkdtemp(prefix="test_cli_images_flag_"))
        try:
            img1 = temp_dir / "f1.png"
            Image.new("RGB", (20, 20), "red").save(img1)
            img2 = temp_dir / "f2.png"
            Image.new("RGB", (20, 20), "blue").save(img2)

            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent
            mock_agent.extract_from_pdf.return_value = "pdf text"

            main([
                "--images", str(img1), str(img2),
                "--raw",
                "--no-progress",
            ])

            mock_agent.extract_from_pdf.assert_called_once()
            called_pdf = mock_agent.extract_from_pdf.call_args[0][0]
            self.assertTrue(called_pdf.lower().endswith(".pdf"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
