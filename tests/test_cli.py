import unittest
from unittest.mock import patch, MagicMock
from src.fieldnotes.cli import build_parser, main

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

    @patch('src.fieldnotes.cli.UnlimitedOCRAgent')
    @patch('builtins.print')
    def test_main_execution_raw(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_image.return_value = "raw ocr output"

        main(["image.png", "--raw"])

        mock_agent_class.assert_called_once()
        mock_agent.extract_from_image.assert_called_once_with("image.png")
        mock_agent.ask_lmstudio.assert_not_called()

    @patch('src.fieldnotes.cli.UnlimitedOCRAgent')
    @patch('builtins.print')
    def test_main_execution_pdf(self, mock_print, mock_agent_class):
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.extract_from_pdf.return_value = "pdf ocr output"

        main(["doc.pdf", "--raw"])
        mock_agent.extract_from_pdf.assert_called_once_with("doc.pdf")

if __name__ == '__main__':
    unittest.main()
