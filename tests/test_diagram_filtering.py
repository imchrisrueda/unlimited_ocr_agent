import unittest
from unittest.mock import MagicMock
from src.fieldnotes.schemas.notebook import NotebookConfig, NotebookPageDTO
from src.fieldnotes.schemas.diagram import DiagramDTO
from src.fieldnotes.profiles.notebook import (
    NotebookProfile,
    CuadernoCampoProfile,
    CuadernoTextPageDTO,
    build_notebook_page_prompt,
    VISUAL_INVENTORY_PROMPT,
)
from src.fieldnotes.cli import build_parser


class TestDiagramFiltering(unittest.TestCase):
    def test_notebook_config_defaults(self):
        cfg = NotebookConfig()
        self.assertTrue(cfg.extract_diagrams)
        self.assertIsNone(cfg.diagram_pages)
        self.assertTrue(cfg.is_diagram_extraction_enabled(1))
        self.assertTrue(cfg.is_diagram_extraction_enabled(2))

    def test_notebook_config_disabled(self):
        cfg = NotebookConfig(extract_diagrams=False)
        self.assertFalse(cfg.extract_diagrams)
        self.assertFalse(cfg.is_diagram_extraction_enabled(1))
        self.assertFalse(cfg.is_diagram_extraction_enabled(2))

    def test_notebook_config_diagram_pages(self):
        cfg = NotebookConfig(diagram_pages=[2, 4])
        self.assertFalse(cfg.is_diagram_extraction_enabled(1))
        self.assertTrue(cfg.is_diagram_extraction_enabled(2))
        self.assertFalse(cfg.is_diagram_extraction_enabled(3))
        self.assertTrue(cfg.is_diagram_extraction_enabled(4))

    def test_notebook_config_invalid_pages(self):
        with self.assertRaises(ValueError):
            NotebookConfig(diagram_pages=[0])
        with self.assertRaises(ValueError):
            NotebookConfig(diagram_pages=[-1])
        with self.assertRaises(ValueError):
            NotebookConfig(diagram_pages=[True])

    def test_notebook_config_diagram_pages_dedup_and_sort(self):
        cfg = NotebookConfig(diagram_pages=[3, 1, 3, 2])
        self.assertEqual(cfg.diagram_pages, [1, 2, 3])

    def test_visual_inventory_prompt_contains_exclusion_rules(self):
        self.assertIn("NUNCA clasifiques como croquis o diagrama fragmentos de texto normal", VISUAL_INVENTORY_PROMPT)
        self.assertIn("fechas enmarcadas", VISUAL_INVENTORY_PROMPT)

    def test_build_notebook_page_prompt_with_diagrams_disabled(self):
        cfg = NotebookConfig(extract_diagrams=False)
        prompt = build_notebook_page_prompt(1, config=cfg)
        self.assertIn("DIRECTIVA DE DIAGRAMAS: La extracción de diagramas/croquis está DESACTIVADA", prompt)

    def test_cuaderno_campo_build_page_prompt_with_diagrams_disabled(self):
        mock_agent = MagicMock()
        mock_agent.base_output_dir = "./output_ocr"
        cfg = NotebookConfig(extract_diagrams=False)
        profile = CuadernoCampoProfile(agent=mock_agent, config=cfg)
        prompt = profile.build_page_prompt(1, None)
        self.assertIn("MODO CUADERNO_CAMPO (TEXTO PURO)", prompt)
        self.assertIn("La extracción de diagramas está DESACTIVADA", prompt)

    def test_cuaderno_campo_enrich_page_dto_skips_when_disabled(self):
        mock_agent = MagicMock()
        mock_agent.base_output_dir = "./output_ocr"
        cfg = NotebookConfig(extract_diagrams=False)
        profile = CuadernoCampoProfile(agent=mock_agent, config=cfg)

        mock_art = MagicMock()
        mock_art.page_number = 1
        page_dto = NotebookPageDTO(page_number=1, sections=[], notes=[])

        res = profile.enrich_page_dto(mock_art, page_dto, max_tokens=1024, call_kwargs={})
        # Should not have called ask_page_vision_structured at all!
        mock_agent.ask_page_vision_structured.assert_not_called()
        self.assertEqual(res.diagrams, [])

    def test_cuaderno_campo_enrich_page_dto_respects_diagram_pages(self):
        mock_agent = MagicMock()
        mock_agent.base_output_dir = "./output_ocr"
        cfg = NotebookConfig(diagram_pages=[2])
        profile = CuadernoCampoProfile(agent=mock_agent, config=cfg)

        # Page 1: disabled
        mock_art_1 = MagicMock()
        mock_art_1.page_number = 1
        page_dto_1 = NotebookPageDTO(page_number=1)
        res_1 = profile.enrich_page_dto(mock_art_1, page_dto_1, max_tokens=1024, call_kwargs={})
        mock_agent.ask_page_vision_structured.assert_not_called()
        self.assertEqual(res_1.diagrams, [])

        # Page 2: enabled, will call ask_page_vision_structured
        mock_art_2 = MagicMock()
        mock_art_2.page_number = 2
        page_dto_2 = NotebookPageDTO(page_number=2)
        mock_inventory = MagicMock()
        mock_inventory.items = []
        mock_agent.ask_page_vision_structured.return_value = mock_inventory
        res_2 = profile.enrich_page_dto(mock_art_2, page_dto_2, max_tokens=1024, call_kwargs={})
        mock_agent.ask_page_vision_structured.assert_called_once()
        self.assertEqual(res_2.diagrams, [])

    def test_cli_parser_diagram_flags(self):
        parser = build_parser()
        # Default
        args = parser.parse_args(["doc.pdf"])
        self.assertFalse(args.no_diagrams)
        self.assertIsNone(args.diagram_pages)

        # --no-diagrams
        args = parser.parse_args(["doc.pdf", "--no-diagrams"])
        self.assertTrue(args.no_diagrams)

        # --text-only alias
        args = parser.parse_args(["doc.pdf", "--text-only"])
        self.assertTrue(args.no_diagrams)

        # --diagram-pages
        args = parser.parse_args(["doc.pdf", "--diagram-pages", "1,3"])
        self.assertEqual(args.diagram_pages, "1,3")


if __name__ == "__main__":
    unittest.main()
